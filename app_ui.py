import os
import streamlit as st
from engine import load_and_merge_excels, run_reconciliation, generate_excel_report
from updater import get_local_version, check_for_update, apply_update, restart_app, REPO_URL, GITHUB_USER

st.set_page_config(page_title="Automated Content Verification", page_icon="🔍", layout="wide")

# CSS hack to remove dead space at the top of the UI
st.markdown("""
    <style>
           .block-container {
                padding-top: 3.5rem;
                padding-bottom: 0rem;
            }
    </style>
    """, unsafe_allow_html=True)

if "uploader_key" not in st.session_state:
    st.session_state["uploader_key"] = 0

script_dir = os.path.dirname(os.path.abspath(__file__))
logo_path = os.path.join(script_dir, "logo.png")

if os.path.exists(logo_path):
    st.image(logo_path, width=200)

st.title("Automated Content Verification Suite")
st.caption("offline verification engine for bilingual (English and Hindi) assessment content.")

# ---------- Version / update check (once per session, silent when offline) ----------
LOCAL_VERSION = get_local_version()
if "update_checked" not in st.session_state:
    st.session_state["update_checked"] = True
    st.session_state["remote_version"] = check_for_update()   # None if offline or already latest
remote_version = st.session_state.get("remote_version")

if remote_version:
    u1, u2 = st.columns([4, 1])
    u1.warning(f"🔔 Update available: v{LOCAL_VERSION} → v{remote_version}")
    if u2.button("⬇️ Update now", type="primary", use_container_width=True):
        update_ok = False
        with st.spinner("Downloading update and installing requirements..."):
            try:
                apply_update()
                update_ok = True
            except Exception as e:
                st.error(f"Update failed: {e}")
        if update_ok:
            st.success("Update installed. Restarting the tool...")
            restart_app()

with st.sidebar:
    if st.button("🔄 Reset / Refresh", type="secondary", use_container_width=True):
        current_key = st.session_state.get("uploader_key", 0)
        st.session_state.clear()
        st.session_state["uploader_key"] = current_key + 1
        st.rerun()
        
    st.markdown("---")
    st.header("1. Data collection")
    
    files_exam = st.file_uploader(
        "Upload Target Data (e.g., Exam/Tool Files)", 
        type=["xlsx", "xls"], 
        accept_multiple_files=True, 
        key=f"exam_uploader_{st.session_state['uploader_key']}"
    )
    
    files_bank = st.file_uploader(
        "Upload Reference Data (e.g., Question Banks)", 
        type=["xlsx", "xls"], 
        accept_multiple_files=True, 
        key=f"bank_uploader_{st.session_state['uploader_key']}"
    )
    
    st.header("2. Verification Settings")
    min_match_thresh = st.slider("Similarity Threshold (%)", min_value=10, max_value=100, value=60, step=5)
    st.markdown("---")
    st.caption(f"Version: v{LOCAL_VERSION}")
    st.caption(f"GitHub: [{GITHUB_USER}]({REPO_URL})")

if not files_exam or not files_bank:
    st.info("Please attach at least one file for both Target Data and Reference Data to proceed.")
    st.stop()

df_exam = load_and_merge_excels(files_exam)
df_bank = load_and_merge_excels(files_bank)

exam_cols_with_none = ["--- None ---"] + list(df_exam.columns)
bank_cols_with_none = ["--- None ---"] + list(df_bank.columns)

st.subheader("Field Mapping Configuration")
col1, col2 = st.columns(2)
with col1:
    st.markdown("**Target Data Parameters**")
    qid_col = st.selectbox("Unique Question ID (QID)", df_exam.columns, index=0)
    q_exam_col = st.selectbox("Question", df_exam.columns, index=min(1, len(df_exam.columns)-1))
    opts_exam_cols = st.multiselect("Options (Leave empty to skip)", df_exam.columns)
    ans_exam_col = st.selectbox("Answer Key", exam_cols_with_none, index=0, help="Select '--- None ---' to bypass Answer Key validation.")
    
with col2:
    st.markdown("**Reference Data Parameters**")
    q_bank_col = st.selectbox("Question", df_bank.columns, index=0)
    opts_bank_cols = st.multiselect("Options (Leave empty to skip)", df_bank.columns)
    ans_bank_col = st.selectbox("Answer Key", bank_cols_with_none, index=0, help="Select '--- None ---' to bypass Answer Key validation.")

if st.button("Execute Verification Scan", type="primary", use_container_width=True):
    progress_bar = st.progress(0)
    status_text = st.empty()

    def update_ui(current, total, msg):
        progress_bar.progress(current / total)
        status_text.text(f"{msg} ({current}/{total})")

    mapping_config = {
        "qid_col": qid_col, "q_exam_col": q_exam_col, 
        "opts_exam_cols": opts_exam_cols, "ans_exam_col": ans_exam_col,
        "q_bank_col": q_bank_col, "opts_bank_cols": opts_bank_cols, "ans_bank_col": ans_bank_col
    }

    df_results = run_reconciliation(df_exam, df_bank, mapping_config, min_match_thresh, update_ui)
    
    st.session_state["match_results"] = df_results
    status_text.empty()
    progress_bar.empty()
    st.success("Verification protocol complete.")

if "match_results" in st.session_state:
    df_res = st.session_state["match_results"]
    st.markdown("---")
    st.header("Diagnostic Audit Report")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Processed Records", len(df_res))
    m2.metric("Exact Matches (100%)", len(df_res[df_res["Status"] == "Exact Match"]))
    m3.metric("High/Partial Matches", len(df_res[df_res["Status"].isin(["High Match", "Partial Match"])]))
    m4.metric("Unmatched Exceptions", len(df_res[df_res["Status"] == "Unmatched"]))

    filter_status = st.multiselect("Filter by Confidence Status", options=["Exact Match", "High Match", "Partial Match", "Low Match", "Unmatched"], default=["Exact Match", "High Match", "Partial Match", "Low Match", "Unmatched"])
    df_filtered = df_res[df_res["Status"].isin(filter_status)]

    st.dataframe(df_filtered[["QID", "Status", "Composite_Match_%", "Question_Match_%", "Options_Match_%", "Key_Match_%", "Exam_Question", "Bank_Matched_Question"]], use_container_width=True, hide_index=True)

    st.subheader("Granular Record Inspector")
    selected_qid = st.selectbox("Select QID to analyze mapping:", df_filtered["QID"].unique())
    if selected_qid:
        row = df_filtered[df_filtered["QID"] == selected_qid].iloc[0]
        ins1, ins2 = st.columns(2)
        with ins1:
            st.markdown(f"#### Target Record (QID: {row['QID']})")
            st.info(row["Exam_Question"])
            st.write("**Choices:**")
            st.text(row["Exam_Options"])
            st.write(f"**Valid Key:** `{row['Exam_Answer']}`")
        with ins2:
            st.markdown(f"#### Reference Match ({row['Bank_Source_File']})")
            st.info(row["Bank_Matched_Question"])
            st.write("**Choices:**")
            st.text(row["Bank_Options"])
            st.write(f"**Valid Key:** `{row['Bank_Answer']}`")

    excel_data = generate_excel_report(df_res, min_match_thresh)
    st.download_button("Export Comprehensive Audit Report (.xlsx)", data=excel_data.getvalue(), file_name="Automated_Content_Audit_Report.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)