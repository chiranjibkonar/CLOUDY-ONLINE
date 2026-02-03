import streamlit as st
import os
import subprocess
import zipfile
import glob
import time
import shutil
import urllib.request

st.set_page_config(page_title="CloudyOnline", layout="wide")

if "sim_results" not in st.session_state:
    st.session_state.sim_results = None

if st.session_state.sim_results is not None:
    if "files" not in st.session_state.sim_results:
        st.session_state.sim_results = None
        st.rerun()

# --- 2. ENGINE SETUP ---
DROPBOX_URL = "https://www.dropbox.com/scl/fi/d8y2x0vvijqupugjrsdyr/cloudy_linux_ready.zip?rlkey=b011qvjfnc7inu2w0uawu0hs5&st=irlslrsz&dl=1"
INSTALL_DIR = "./cloudy_install"
ZIP_NAME = "cloudy_linux_ready.zip"

def setup_cloudy():
    if os.path.exists(f"{INSTALL_DIR}") and glob.glob(f"{INSTALL_DIR}/**/checksums.dat", recursive=True):
        return

    with st.spinner("Initializing Cloudy Engine..."):
        try:
            if not os.path.exists(ZIP_NAME):
                urllib.request.urlretrieve(DROPBOX_URL, ZIP_NAME)
            
            with zipfile.ZipFile(ZIP_NAME, 'r') as zip_ref:
                zip_ref.extractall(INSTALL_DIR)
            
            if os.path.exists(ZIP_NAME):
                os.remove(ZIP_NAME)
        except Exception as e:
            st.error(f"Setup Failed: {e}")
            st.stop()

setup_cloudy()

exe_list = glob.glob(f"{INSTALL_DIR}/**/cloudy.exe", recursive=True) + \
           glob.glob(f"{INSTALL_DIR}/**/source/source", recursive=True)
data_list = glob.glob(f"{INSTALL_DIR}/**/checksums.dat", recursive=True)

if not exe_list or not data_list:
    st.error("Critical: Engine files missing.")
    st.stop()

CLOUDY_EXE = os.path.abspath(exe_list[0])
CLOUDY_DATA_DIR = os.path.dirname(os.path.abspath(data_list[0])) + os.path.sep
os.chmod(CLOUDY_EXE, 0o777)


st.title("CloudyOnline: Spectral Synthesis for Astrophysicists")

with st.sidebar:
    st.header("Configuration")
    
    st.subheader("1. Radiation Field (SED)")
    sed_type = st.selectbox("Type", ["Built-in AGN", "Upload File", "Power Law", "Blackbody", "Background (HM12)"])
    
    sed_command = ""
    sed_ready = True
    
    if sed_type == "Built-in AGN":
        sed_command = "table AGN"
    elif sed_type == "Power Law":
        slope = st.number_input("Slope (alpha) [Dimensionless]", value=-1.0, step=0.1)
        sed_command = f"table power law {slope}"
    elif sed_type == "Blackbody":
        temp = st.number_input("Temperature [Kelvin]", value=100000.0, step=1000.0)
        sed_command = f"table blackbody {temp}"
    elif sed_type == "Background (HM12)":
        z = st.number_input("Redshift (z) [Dimensionless]", value=0.0, step=0.1)
        sed_command = f"table HM12 z={z}"

    elif sed_type == "Upload File":
        st.info("⚠️ **File Format Requirements:**\n\n1. **Column 1:** Energy in **Rydbergs** (Linear)\n2. **Column 2:** Flux or Luminosity (Linear)")
        
        col_units = st.radio(
            "Select Column 2 Units:",
            ["nuFnu (or nuLnu)", "Fnu (or Lnu)"],
            help="nuFnu = Energy/s (SED). Fnu = Energy/s/Hz (Flux Density).",
            horizontal=True
        )
        st.caption("ℹ️ **Note:** Luminosity (L) and Flux (F) can be used interchangeably to set the spectral **shape**.")
        
        unit_keyword = "nuFnu" if "nuFnu" in col_units else "Fnu"

        uploaded_sed = st.file_uploader("Upload .txt/.sed", type=["txt", "sed", "dat"])
        sed_ready = False
        if uploaded_sed:
            sed_filename = "user_sed.dat"
            target_path = os.path.join(CLOUDY_DATA_DIR, sed_filename)
            content = uploaded_sed.getvalue().decode("utf-8", errors="ignore")
            lines = content.splitlines()
            data_points = []
            for line in lines:
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        e, f = float(parts[0]), float(parts[1])
                        data_points.append((e, f))
                    except ValueError: continue
            data_points.sort(key=lambda x: x[0])
            if data_points:
                with open(target_path, "w", encoding="ascii") as f:
                    last_e = -1.0
                    for e, flux in data_points:
                        if e > last_e:
                            f.write(f"{e:.6e}  {flux:.6e}\n")
                            last_e = e
                    f.flush()
                    os.fsync(f.fileno())
                st.success(f"Loaded {len(data_points)} pts")
                sed_command = f'table SED "{sed_filename}" linear {unit_keyword}'
                sed_ready = True
            else:
                st.error("Invalid File")

    st.subheader("2. Intensity Command")
    int_mode = st.radio("Define Intensity by:", ["Luminosity (Total)", "Ionization Parameter (U)"])
    int_command = ""
    if int_mode == "Luminosity (Total)":
        lum = st.text_input("Bolometric Luminosity [erg s^-1]", "1.283E+44 linear")
        int_command = f"Luminosity total {lum}"
    else:
        u_val = st.number_input("Ionization Parameter [log U]", value=-1.0, step=0.1)
        int_command = f"ionization parameter {u_val}"

    st.subheader("3. Medium Properties")
    hden = st.number_input("Hydrogen Density [log cm^-3]", value=5.70000, step=0.00001, format="%.5f")
    abund = st.selectbox("Chemical Abundances", ["ISM", "Solar", "Primordial", "H II Region"])
    grains = st.checkbox("Include Dust Grains?", True)

    st.subheader("4. Simulation Logic")
    do_iterate = st.checkbox("Iterate to Convergence", value=True, help="Highly recommended. Runs multiple passes until physics stabilize.")
    do_failures = st.checkbox("Prevent Early Crashes", value=True, help="Adds 'failures 1000' to prevent the sim from stopping if math gets stuck.")

    st.subheader("Output Files")
    with st.expander("Select Files to Save", expanded=False):
        save_opts = st.multiselect(
            "Choose auxiliary files:",
            ["Overview (.ovr)", "Continuum (.con)", "Heating (.het)", "Cooling (.col)", "Pressure (.pre)", "Grain Opacity (.opc)", "Hydrogen Ionization (.hyd)"],
            default=["Overview (.ovr)", "Continuum (.con)"]
        )
    
    st.info("Configure simulation parameters here, then run using the button on the main panel.")


c1, c2 = st.columns(2)

with c1:
    st.info("Geometry Settings")
    geo_shape = st.radio("Shape", ["Open Geometry", "Sphere", "Cylinder"], horizontal=True)
    cov = st.slider("Covering Factor [0.0 - 1.0]", 0.0, 1.0, 0.1)
    
    geo_cmds = []
    if geo_shape == "Sphere": geo_cmds.append("sphere")
    if geo_shape == "Cylinder": geo_cmds.append("cylinder")
    geo_cmds.append(f"covering factor {cov}")

with c2:
    st.warning("Stopping Criteria")
    st.caption("Radial Extent")
    r_in = st.text_input("Inner Radius [cm] (Start)", "2.953E+19 linear")
    
    use_r_out = st.checkbox("Set Outer Radius (Stop)", value=True)
    r_out_cmd = ""
    if use_r_out:
        r_out = st.text_input("Outer Radius [cm] (Stop)", "2.953E+21 linear")
        r_out_cmd = f"stop radius {r_out}"
    
    st.caption("Physical Limits")
    use_col = st.checkbox("Stop at Column Density")
    col_cmd = ""
    if use_col:
        val = st.number_input("Column Density [log cm^-2]", value=21.0)
        col_cmd = f"stop column density {val}"
        
    use_temp = st.checkbox("Stop at Low Temperature")
    temp_cmd = ""
    if use_temp:
        val = st.number_input("Min Temperature [Kelvin]", value=4000.0)
        temp_cmd = f"stop temperature {val}"

# --- PREPARE COMMANDS (Live Preview) ---
cmds = []
file_map = {}

if sed_ready:
    # Build Command List
    cmds = ["title Streamlit Run", sed_command, int_command]
    cmds.append(f"hden {hden}")
    cmds.append(f"abundances {abund}")
    if grains: cmds.append("grains ISM")
    
    if do_iterate:
        cmds.append("iterate to convergence")
    if do_failures:
        cmds.append("failures 1000")

    cmds.append(f"radius {r_in}")
    if use_r_out: cmds.append(r_out_cmd)
    if use_col: cmds.append(col_cmd)
    if use_temp: cmds.append(temp_cmd)
    cmds.extend(geo_cmds)
    
    file_map = {"out": "temp.out", "log": "run.log"} 
    
    if "Overview (.ovr)" in save_opts:
        cmds.append('save overview "temp.ovr" last')
        file_map["ovr"] = "temp.ovr"
    if "Continuum (.con)" in save_opts:
        cmds.append('save continuum "temp.con" last units Angstroms')
        file_map["con"] = "temp.con"
    if "Heating (.het)" in save_opts:
        cmds.append('save heating "temp.het" last')
        file_map["het"] = "temp.het"
    if "Cooling (.col)" in save_opts:
        cmds.append('save cooling "temp.col" last')
        file_map["col"] = "temp.col"
    if "Pressure (.pre)" in save_opts:
        cmds.append('save pressure "temp.pre" last')
        file_map["pre"] = "temp.pre"
    if "Grain Opacity (.opc)" in save_opts:
        cmds.append('save grain opacity "temp.opc" last')
        file_map["opc"] = "temp.opc"
    if "Hydrogen Ionization (.hyd)" in save_opts:
        cmds.append('save element hydrogen "temp.hyd" last')
        file_map["hyd"] = "temp.hyd"

    st.subheader("📝 Input Script Preview")
    with st.expander("View generated .in file", expanded=True):
        st.code("\n".join(cmds), language="text")
        st.caption("This script will be sent to the Cloudy engine when you click Run.")

if st.button("Run Simulation", type="primary", use_container_width=True):
    if not sed_ready:
        st.error("Please fix SED settings or upload a valid file.")
    else:
        with open("temp.in", "w") as f:
            f.write("\n".join(cmds))
            f.flush()
            os.fsync(f.fileno())
            
        possible_exts = ["out", "ovr", "con", "het", "col", "pre", "opc", "hyd", "log"]
        for ext in possible_exts:
            fname = "run.log" if ext == "log" else f"temp.{ext}"
            if os.path.exists(fname): os.remove(fname)

        env = os.environ.copy()
        env["CLOUDY_DATA_PATH"] = CLOUDY_DATA_DIR
        
        status = st.empty()
        pbar = st.progress(0)
        
        try:
            with open("run.log", "w") as lf:
                proc = subprocess.Popen([CLOUDY_EXE, "-r", "temp"], stdout=lf, stderr=lf, env=env)
            
            start = time.time()
            while proc.poll() is None:
                el = int(time.time() - start)
                status.info(f"Simulation Running... {el}s")
                pbar.progress(min(el % 100, 100))
                time.sleep(1)
            
            res = {"success": (proc.returncode == 0), "files": {}}
            
            for key, fname in file_map.items():
                if os.path.exists(fname):
                    with open(fname, "r") as f:
                        res["files"][key] = f.read()
            
            st.session_state.sim_results = res
            status.empty()
            pbar.empty()
            
        except Exception as e:
            st.error(f"Error: {e}")

if st.session_state.sim_results:
    r = st.session_state.sim_results
    if "files" not in r: st.stop()
    files = r["files"]
    
    if r["success"]:
        version_str = "Unknown Version"
        if "out" in files:
            for line in files["out"].splitlines()[:20]:
                if "Cloudy" in line and ("master" in line or "gold" in line or "c17" in line):
                    version_str = line.strip()
                    break

        st.success(f"Simulation Complete! | Engine: {version_str}")
        
        st.write("### Downloads")
        cols = st.columns(4) 
        
        name_map = {
            "out": "Main Output (.out)",
            "ovr": "Overview (.ovr)",
            "con": "Continuum (.con)",
            "het": "Heating (.het)",
            "col": "Cooling (.col)",
            "pre": "Pressure (.pre)",
            "opc": "Opacity (.opc)",
            "hyd": "Hydrogen (.hyd)"
        }
        
        idx = 0
        for key, content in files.items():
            if key == "log": continue 
            
            label = name_map.get(key, f"File .{key}")
            with cols[idx % 4]:
                st.download_button(
                    label=f"Download {label}",
                    data=content,
                    file_name=f"cloudy_sim.{key}",
                    mime="text/plain"
                )
            idx += 1

    else:
        st.error("Crashed")
        st.warning("Engine Log:")
        st.code(files.get("log", "No log available"))

st.markdown("---")
col_footer1, col_footer2 = st.columns([1, 1])

with col_footer1:
    st.subheader("About the Creator")
    st.markdown("**Pranjal Sharma**")
    st.markdown("[View Portfolio](https://pranjalsh22.wixsite.com/pranjalsharma)")
    st.markdown("[Email Suggestions and Ideas](mailto:Ps.Sharmapranjal@gmail.com)")
    st.markdown("**Non-Commercial License:** Free for personal/research use. Commercial sale prohibited.")

with col_footer2:
    st.subheader("Powered by Cloudy")
    st.markdown("This tool uses the [Cloudy](https://nublado.org/) engine (Ferland et al., Univ. of Kentucky).")
    st.markdown("Users publishing research using this tool must cite the Cloudy release as defined on their website.")

st.markdown("---")
st.subheader("My Scientific Tools:")

apps = [
    {"name": "Cloudy Online", "url": "https://cloudyonline.streamlit.app/", "desc": "Online interface for Cloudy spectral synthesis."},
    {"name": "Cloudy Interpreter", "url": "https://cloudy-output-interpreter.streamlit.app/", "desc": "Analyze and visualize Cloudy output files."},
    {"name": "Accretion Disk Sim", "url": "https://accretion-disk-spectrum.streamlit.app/", "desc": "Standard accretion disk spectrum simulator."},
    {"name": "Dark Matter Estimator", "url": "https://darkmatter.streamlit.app/", "desc": "Rotation curves and dark matter halo estimation."},
    {"name": "GraphAway", "url": "https://graphaway.streamlit.app/", "desc": "Advanced plotting and graphing tool for researchers."}
]

cols = st.columns(3)
for i, app in enumerate(apps):
    with cols[i % 3]:
        st.markdown(f"#### [{app['name']}]({app['url']})")
        st.caption(app['desc'])
        st.markdown("---")
#-------user-analytics-----
st.markdown(""" <script defer src="https://cloud.umami.is/script.js"     data-website-id="10d7ceae-d3f2-42d2-ba29-25e2082cc088"></script> """, unsafe_allow_html=True)
#---------------------------
st.write("Thank you for visiting.")
