import io
import re
import math
import unicodedata
from typing import List, Tuple, Callable
import numpy as np
import pandas as pd
from rapidfuzz import fuzz

def parse_cell_value(val) -> str:
    """Safely extracts cell values. Booleans stay as True/False, whole-number floats lose the .0."""
    if val is None or pd.isna(val):
        return ""
    if isinstance(val, (bool, np.bool_)):
        return "True" if val else "False"
    if isinstance(val, float) and val.is_integer():
        return str(int(val))
    text = str(val).strip()
    # Whole-cell "true"/"false" typed as text is normalised to match real booleans
    if text.lower() in ("true", "false"):
        return "True" if text.lower() == "true" else "False"
    return text

def format_display_text(val) -> str:
    text = parse_cell_value(val)
    if not text: return ""
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def clean_text(val) -> str:
    text = parse_cell_value(val)
    if not text: return ""
    text = unicodedata.normalize('NFC', text)
    text = re.sub(r"(?i)<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def calculate_question_similarity(q1: str, q2: str) -> float:
    q1_clean, q2_clean = clean_text(q1), clean_text(q2)
    if not q1_clean or not q2_clean: return 0.0
    if q1_clean == q2_clean: return 100.0

    seq_ratio = fuzz.ratio(q1_clean, q2_clean, processor=None)
    token_sort = fuzz.token_sort_ratio(q1_clean, q2_clean, processor=None)
    return round((0.85 * seq_ratio) + (0.15 * token_sort), 2)

def calculate_options_similarity(opts_a: List[str], opts_b: List[str]) -> Tuple[float, str, str]:
    clean_a = [clean_text(o) for o in opts_a if clean_text(o)]
    clean_b = [clean_text(o) for o in opts_b if clean_text(o)]

    if not clean_a and not clean_b:
        return 100.0, "None", "None"
    if not clean_a or not clean_b:
        return 0.0, "\n".join(clean_a) or "None", "\n".join(clean_b) or "None"

    unmatched_b = list(clean_b)
    scores = []
    for opt_a in clean_a:
        if not unmatched_b:
            scores.append(0.0)
            continue
        best_match_idx, best_score = -1, -1.0
        for idx, opt_b in enumerate(unmatched_b):
            score = fuzz.ratio(opt_a, opt_b, processor=None)
            if score > best_score:
                best_score, best_match_idx = score, idx
        scores.append(best_score)
        if best_match_idx != -1: 
            unmatched_b.pop(best_match_idx)

    count_penalty = min(len(clean_a), len(clean_b)) / max(len(clean_a), len(clean_b))
    avg_score = (sum(scores) / len(scores)) * count_penalty
    
    # Options combined with newlines for clean cell formatting
    opts_a_repr = "\n".join([f"({i+1}) {format_display_text(o)}" for i, o in enumerate(opts_a) if pd.notna(o)])
    opts_b_repr = "\n".join([f"({i+1}) {format_display_text(o)}" for i, o in enumerate(opts_b) if pd.notna(o)])
    return round(avg_score, 2), opts_a_repr, opts_b_repr

def calculate_key_similarity(ans_a: str, ans_b: str) -> float:
    a, b = clean_text(ans_a), clean_text(ans_b)
    if not a and not b: return 100.0
    if not a or not b: return 0.0
    if a == b: return 100.0
    return round(fuzz.ratio(a, b, processor=None), 2)

def load_and_merge_excels(uploaded_files) -> pd.DataFrame:
    dfs = []
    for f in uploaded_files:
        # Dropped dtype=str so Pandas reads booleans as actual booleans, allowing parse_cell_value to catch them
        df = pd.read_excel(f)
        df["_Source_File"] = f.name
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

def run_reconciliation(df_exam: pd.DataFrame, df_bank: pd.DataFrame, mappings: dict, min_match_thresh: int, progress_callback: Callable = None) -> pd.DataFrame:
    results = []
    total_exam_rows = len(df_exam)

    has_opts = bool(mappings['opts_exam_cols']) and bool(mappings['opts_bank_cols'])
    has_key = mappings['ans_exam_col'] != "--- None ---" and mappings['ans_bank_col'] != "--- None ---"

    for i, (_, row_exam) in enumerate(df_exam.iterrows()):
        qid = parse_cell_value(row_exam[mappings['qid_col']])
        if progress_callback: progress_callback(i + 1, total_exam_rows, f"Processing row {i+1} of {total_exam_rows} (QID: {qid})...")

        q_text_a = format_display_text(row_exam[mappings['q_exam_col']])
        
        opts_a = []
        if mappings['opts_exam_cols']:
            opts_a = [parse_cell_value(row_exam[c]) for c in mappings['opts_exam_cols'] if pd.notna(row_exam[c])]
            
        ans_a = ""
        if mappings['ans_exam_col'] != "--- None ---" and pd.notna(row_exam[mappings['ans_exam_col']]):
            ans_a = format_display_text(row_exam[mappings['ans_exam_col']])

        best_match = {
            "QID": qid, "Exam_Question": q_text_a, "Bank_Matched_Question": "No match found",
            "Question_Match_%": 0.0, "Options_Match_%": 0.0, "Key_Match_%": 0.0, "Composite_Match_%": 0.0,
            "Exam_Options": "\n".join([f"({idx+1}) {format_display_text(o)}" for idx, o in enumerate(opts_a)]) if opts_a else "None", 
            "Bank_Options": "", "Exam_Answer": ans_a if ans_a else "None", "Bank_Answer": "", 
            "Bank_Source_File": "", "Status": "Unmatched"
        }
        best_composite = -1.0

        for _, row_bank in df_bank.iterrows():
            q_text_b_disp = format_display_text(row_bank[mappings['q_bank_col']])
            q_score = calculate_question_similarity(q_text_a, q_text_b_disp)
            
            if q_score < 30.0: continue

            opt_score, opt_a_fmt, opt_b_fmt = 0.0, best_match["Exam_Options"], "None"
            if has_opts:
                opts_b = [parse_cell_value(row_bank[c]) for c in mappings['opts_bank_cols'] if pd.notna(row_bank[c])]
                opt_score, opt_a_fmt, opt_b_fmt = calculate_options_similarity(opts_a, opts_b)

            key_score, ans_b_disp = 0.0, "None"
            if has_key:
                raw_ans_b = row_bank[mappings['ans_bank_col']] if pd.notna(row_bank[mappings['ans_bank_col']]) else ""
                ans_b_disp = format_display_text(raw_ans_b)
                key_score = calculate_key_similarity(ans_a, ans_b_disp)

            if has_opts and has_key:
                composite = round((0.50 * q_score) + (0.30 * opt_score) + (0.20 * key_score), 2)
            elif has_opts and not has_key:
                composite = round((0.60 * q_score) + (0.40 * opt_score), 2)
            elif not has_opts and has_key:
                composite = round((0.70 * q_score) + (0.30 * key_score), 2)
            else:
                composite = round(q_score, 2)

            if composite > best_composite:
                best_composite = composite
                status = "High Match" if composite >= 90 else ("Partial Match" if composite >= min_match_thresh else "Low Match")
                
                exact_q = (q_score == 100)
                exact_o = (opt_score == 100) if has_opts else True
                exact_k = (key_score == 100) if has_key else True
                
                if exact_q and exact_o and exact_k:
                    composite = 100.0
                    status = "Exact Match"

                best_match.update({
                    "Bank_Matched_Question": q_text_b_disp, "Question_Match_%": q_score, 
                    "Options_Match_%": opt_score if has_opts else "N/A",
                    "Key_Match_%": key_score if has_key else "N/A", 
                    "Composite_Match_%": composite, 
                    "Exam_Options": opt_a_fmt, "Bank_Options": opt_b_fmt, 
                    "Exam_Answer": ans_a if ans_a else "None", "Bank_Answer": ans_b_disp,
                    "Bank_Source_File": row_bank.get("_Source_File", "Bank"), "Status": status
                })

        if best_match["Composite_Match_%"] < min_match_thresh:
            best_match["Status"] = "Unmatched"
            
        results.append(best_match)

    return pd.DataFrame(results)

# ---------------- Report layout config ----------------
COLUMN_ORDER = [
    "QID",
    "Exam_Question", "Bank_Matched_Question", "Question_Match_%",
    "Exam_Options", "Bank_Options", "Options_Match_%",
    "Exam_Answer", "Bank_Answer", "Key_Match_%",
    "Composite_Match_%",
    "Bank_Source_File",
    "Status",
]
MATCH_COLS = ["Question_Match_%", "Options_Match_%", "Key_Match_%", "Composite_Match_%"]
TEXT_COLS = ["Exam_Question", "Bank_Matched_Question", "Exam_Options", "Bank_Options"]
WIDTHS = {
    "QID": 12,
    "Exam_Question": 50, "Bank_Matched_Question": 50, "Question_Match_%": 16,
    "Exam_Options": 45, "Bank_Options": 45, "Options_Match_%": 16,
    "Exam_Answer": 14, "Bank_Answer": 14, "Key_Match_%": 14,
    "Composite_Match_%": 18, "Bank_Source_File": 28, "Status": 18,
}
REVIEW_OPTIONS = ["Pending", "Reviewed - OK", "Needs Fix", "Duplicate", "Rejected"]


def generate_excel_report(df_res: pd.DataFrame, min_match_thresh: int) -> io.BytesIO:
    ordered = [c for c in COLUMN_ORDER if c in df_res.columns]
    extras = [c for c in df_res.columns if c not in ordered]
    df = df_res[ordered + extras].copy()
    n = len(df)

    output_stream = io.BytesIO()
    with pd.ExcelWriter(output_stream, engine="xlsxwriter") as writer:
        wb = writer.book

        # ---------- formats (every table cell gets the same thin grey border) ----------
        BORDER = {"border": 1, "border_color": "#7F7F7F"}

        def mk(**kw):
            return wb.add_format({**BORDER, **kw})

        hdr = mk(bold=True, bg_color="#1F4E79", font_color="white",
                 align="center", valign="vcenter", text_wrap=True)
        trk_hdr = mk(bold=True, bg_color="#7F6000", font_color="white",
                     align="center", valign="vcenter", text_wrap=True)
        lbl = mk(align="left", valign="vcenter", indent=1)                          # row labels
        lbl_b = mk(bold=True, bg_color="#F2F2F2", align="left", valign="vcenter", indent=1)
        ctr = mk(align="center", valign="vcenter", text_wrap=True)                 # generic centred cell
        num = mk(align="center", valign="vcenter", num_format="0")
        num_b = mk(bold=True, bg_color="#F2F2F2", align="center", valign="vcenter", num_format="0")
        pct = mk(align="center", valign="vcenter", num_format="0.0")
        pct_b = mk(bold=True, bg_color="#F2F2F2", align="center", valign="vcenter", num_format="0.0")
        left_wrap = mk(align="left", valign="vcenter", text_wrap=True, indent=1)   # question / option text

        title = wb.add_format({"bold": True, "font_size": 16, "font_color": "#1F4E79"})
        subtitle = wb.add_format({"italic": True, "font_color": "#595959"})
        section = wb.add_format({"bold": True, "font_size": 12, "font_color": "#1F4E79",
                                 "bottom": 2, "bottom_color": "#1F4E79"})

        exact_fmt = wb.add_format({"bg_color": "#00B050", "font_color": "white"})
        high_fmt = wb.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"})
        low_fmt = wb.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})

        def color_match(ws, data, first_row):
            if len(data) == 0:
                return
            last_row = first_row + len(data) - 1
            for name in MATCH_COLS:
                if name not in data.columns:
                    continue
                c = data.columns.get_loc(name)
                ws.conditional_format(first_row, c, last_row, c, {"type": "cell", "criteria": "==", "value": 100, "format": exact_fmt})
                ws.conditional_format(first_row, c, last_row, c, {"type": "cell", "criteria": "between", "minimum": 90, "maximum": 99.99, "format": high_fmt})
                ws.conditional_format(first_row, c, last_row, c, {"type": "cell", "criteria": "<", "value": min_match_thresh, "format": low_fmt})

        def est_lines(text, col_width):
            """Rough number of wrapped lines a text needs in a column of given width."""
            chars_per_line = max(int(col_width * 0.9), 1)
            return sum(math.ceil(max(len(part), 1) / chars_per_line) for part in str(text).split("\n"))

        statuses = list(df["Status"].dropna().unique()) if "Status" in df.columns else []
        num_of = lambda col: pd.to_numeric(df[col], errors="coerce")   # "N/A" -> NaN

        # ================= Sheet 1: Master_Tracker (counts / data report) =================
        ws = wb.add_worksheet("Master_Tracker")
        writer.sheets["Master_Tracker"] = ws
        ws.activate()
        ws.set_first_sheet()
        ws.hide_gridlines(2)
        ws.set_column(0, 0, 30)
        ws.set_column(1, 8, 16)
        ws.write(0, 0, "Content Audit - Master Tracker", title)
        ws.write(1, 0, f"Low-match threshold: below {min_match_thresh}%", subtitle)
        row = 3

        # --- 1. Overall status count ---
        ws.write(row, 0, "1. Overall Status Count", section)
        row += 1
        for c, h in enumerate(["Status", "Count", "% of Total"]):
            ws.write(row, c, h, hdr)
        row += 1
        for s in statuses:
            cnt = int((df["Status"] == s).sum())
            ws.write(row, 0, s, lbl)
            ws.write_number(row, 1, cnt, num)
            ws.write_number(row, 2, round(cnt / n * 100, 1) if n else 0, pct)
            row += 1
        ws.write(row, 0, "Total Questions", lbl_b)
        ws.write_number(row, 1, n, num_b)
        ws.write_number(row, 2, 100 if n else 0, num_b)
        row += 2

        # --- 2. Match band distribution ---
        ws.write(row, 0, "2. Match Band Distribution (count of questions)", section)
        row += 1
        bands = [
            ("100% (Exact)", lambda s: s == 100),
            ("90 - 99.99%", lambda s: (s >= 90) & (s < 100)),
            (f"{min_match_thresh} - 89.99%", lambda s: (s >= min_match_thresh) & (s < 90)),
            (f"Below {min_match_thresh}%", lambda s: s < min_match_thresh),
        ]
        metrics = [m for m in MATCH_COLS if m in df.columns]
        ws.write(row, 0, "Match Band", hdr)
        for c, m in enumerate(metrics, start=1):
            ws.write(row, c, m.replace("_", " "), hdr)
        row += 1
        for label, fn in bands:
            ws.write(row, 0, label, lbl)
            for c, m in enumerate(metrics, start=1):
                ws.write_number(row, c, int(fn(num_of(m)).sum()), num)
            row += 1
        ws.write(row, 0, "Average Match %", lbl_b)
        for c, m in enumerate(metrics, start=1):
            avg = num_of(m).mean()
            ws.write_number(row, c, 0 if pd.isna(avg) else round(float(avg), 1), pct_b)
        row += 2

        # --- 3. Answer key agreement ---
        if "Key_Match_%" in df.columns and num_of("Key_Match_%").notna().any():
            ws.write(row, 0, "3. Answer Key Agreement", section)
            row += 1
            for c, h in enumerate(["Result", "Count", "% of Total"]):
                ws.write(row, c, h, hdr)
            row += 1
            k = num_of("Key_Match_%").dropna()
            agree = int((k == 100).sum())
            for label, val in [("Keys Matching", agree), ("Keys Not Matching", len(k) - agree)]:
                ws.write(row, 0, label, lbl)
                ws.write_number(row, 1, val, num)
                ws.write_number(row, 2, round(val / len(k) * 100, 1), pct)
                row += 1
            row += 1

        # --- 4. Bank source file breakdown ---
        if "Bank_Source_File" in df.columns:
            ws.write(row, 0, "4. Bank Source File Breakdown", section)
            row += 1
            heads = ["Source File", "Total"] + statuses + ["Avg Composite %"]
            for c, h in enumerate(heads):
                ws.write(row, c, h, hdr)
            row += 1
            src = df["Bank_Source_File"].replace("", "(no match)").fillna("(no match)")
            for f in src.unique():
                mask = src == f
                sub = df[mask]
                ws.write(row, 0, f, lbl)
                ws.write_number(row, 1, len(sub), num)
                for c, s in enumerate(statuses, start=2):
                    ws.write_number(row, c, int((sub["Status"] == s).sum()), num)
                avg = num_of("Composite_Match_%")[mask].mean() if "Composite_Match_%" in df.columns else 0
                ws.write_number(row, 2 + len(statuses), 0 if pd.isna(avg) else round(float(avg), 1), pct)
                row += 1
            row += 1

        # --- 5. QID-wise tracker (no question / option / key text) ---
        ws.write(row, 0, "5. QID-wise Tracker", section)
        row += 1
        log_cols = [c for c in ["QID"] + MATCH_COLS + ["Bank_Source_File", "Status"] if c in df.columns]
        log = df[log_cols]
        review_hdrs = ["Review_Status", "Reviewer_Remarks"]
        hdr_row = row
        for c, h in enumerate(log_cols):
            ws.write(hdr_row, c, h.replace("_", " "), hdr)
        for j, h in enumerate(review_hdrs):
            ws.write(hdr_row, len(log_cols) + j, h.replace("_", " "), trk_hdr)

        for r, rec in enumerate(log.itertuples(index=False), start=hdr_row + 1):
            for c, v in enumerate(rec):
                if pd.isna(v):
                    ws.write_blank(r, c, None, ctr)
                else:
                    ws.write(r, c, v, ctr)
            for j in range(len(review_hdrs)):                       # bordered empty review cells
                ws.write_blank(r, len(log_cols) + j, None, ctr)

        if n:
            first, last = hdr_row + 1, hdr_row + n
            rs = len(log_cols)
            ws.data_validation(first, rs, last, rs, {"validate": "list", "source": REVIEW_OPTIONS})
            color_match(ws, log, first)
            ws.autofilter(hdr_row, 0, last, len(log_cols) + len(review_hdrs) - 1)

        ws.set_column(len(log_cols), len(log_cols), 18)
        ws.set_column(len(log_cols) + 1, len(log_cols) + 1, 40)
        if "Bank_Source_File" in log_cols:
            b = log_cols.index("Bank_Source_File")
            ws.set_column(b, b, 28)

        # ================= Sheet 2: Audit_Summary (full detail) =================
        ws = wb.add_worksheet("Audit_Summary")
        writer.sheets["Audit_Summary"] = ws
        ws.hide_gridlines(2)
        ws.set_row(0, 32)

        for c, name in enumerate(df.columns):
            ws.write(0, c, name.replace("_", " "), hdr)
            ws.set_column(c, c, WIDTHS.get(name, 22))

        for r, rec in enumerate(df.itertuples(index=False), start=1):
            max_lines = 1
            for c, v in enumerate(rec):
                name = df.columns[c]
                is_text = name in TEXT_COLS
                fmt = left_wrap if is_text else ctr
                if pd.isna(v):
                    ws.write_blank(r, c, None, fmt)
                else:
                    ws.write(r, c, v, fmt)
                if is_text and not pd.isna(v):
                    max_lines = max(max_lines, est_lines(v, WIDTHS.get(name, 22)))
            ws.set_row(r, min(max(max_lines * 15 + 8, 30), 409))    # padding + row height cap

        color_match(ws, df, 1)
        ws.freeze_panes(1, 1)
        ws.autofilter(0, 0, max(n, 1), len(df.columns) - 1)

    output_stream.seek(0)
    return output_stream