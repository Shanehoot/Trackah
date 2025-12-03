import streamlit as st
import google.generativeai as genai
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import json
import re
import os

# --- CONFIGURATION & SETUP ---
st.set_page_config(page_title="AI Macro Tracker", layout="wide", page_icon="🧬")

# Try to get API key from secrets, otherwise ask user
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
except (FileNotFoundError, KeyError):
    # Fallback for local testing or if secrets aren't set
    API_KEY = "YOUR_API_KEY_HERE" 

# --- DATABASE MANAGEMENT ---
DB_NAME = 'fitness_data.db'

def get_db_connection():
    return sqlite3.connect(DB_NAME)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, height_cm REAL, weight_kg REAL, 
                  bf_percent REAL, activity_level TEXT, 
                  goal TEXT, diet_type TEXT,
                  target_calories REAL, target_protein REAL, 
                  target_carbs REAL, target_fats REAL)''')
    
    # Migration for users table
    try:
        c.execute("ALTER TABLE users ADD COLUMN goal TEXT")
        c.execute("ALTER TABLE users ADD COLUMN diet_type TEXT")
    except sqlite3.OperationalError:
        pass

    # Food logs table - EXPANDED for new fields
    c.execute('''CREATE TABLE IF NOT EXISTS food_logs 
                 (id INTEGER PRIMARY KEY, date TEXT, food_name TEXT, 
                  amount_desc TEXT, calories INTEGER, 
                  protein INTEGER, carbs INTEGER, fats INTEGER, 
                  fiber INTEGER, sugar INTEGER, sodium INTEGER,
                  saturated_fat INTEGER, 
                  vitamin_a INTEGER, vitamin_c INTEGER, vitamin_d INTEGER,
                  calcium INTEGER, iron INTEGER, potassium INTEGER, 
                  magnesium INTEGER, zinc INTEGER,
                  nutrients TEXT, note TEXT)''')
    
    # Migration for food_logs - Add all new columns if they don't exist
    new_cols = [
        "fiber", "sugar", "sodium", "note", "saturated_fat", 
        "vitamin_a", "vitamin_c", "vitamin_d", 
        "calcium", "iron", "potassium", "magnesium", "zinc"
    ]
    for col in new_cols:
        try:
            col_type = "TEXT" if col == "note" else "INTEGER"
            c.execute(f"ALTER TABLE food_logs ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    # Body Stats table
    c.execute('''CREATE TABLE IF NOT EXISTS body_stats 
                 (id INTEGER PRIMARY KEY, date TEXT, weight_kg REAL, bf_percent REAL)''')
    
    conn.commit()
    conn.close()

# --- UTILITIES ---
def extract_json(text):
    try:
        clean_text = text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text.replace("```json", "").replace("```", "")
        return json.loads(clean_text)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except:
                pass
        return None

# --- CALCULATIONS ---
def calculate_macros(weight, height, bf_percent, activity_level, goal, diet_type):
    # Katch-McArdle Formula
    lean_mass_kg = weight * (1 - (bf_percent / 100))
    bmr = 370 + (21.6 * lean_mass_kg)
    
    activity_multipliers = {
        "Sedentary": 1.2, "Lightly Active": 1.375,
        "Moderately Active": 1.55, "Very Active": 1.725
    }
    tdee = bmr * activity_multipliers.get(activity_level, 1.2)
    
    if goal == "Lose Weight":
        target_calories = round(tdee - 500)
    elif goal == "Gain Muscle":
        target_calories = round(tdee + 300)
    else:
        target_calories = round(tdee)
    
    if diet_type == "Keto":
        target_carbs = 30
        target_protein = round(lean_mass_kg * 2.0)
        rem_cals = target_calories - ((target_protein * 4) + (target_carbs * 4))
        target_fats = round(max(0, rem_cals / 9))
    elif diet_type == "High Protein":
        target_protein = round(lean_mass_kg * 2.6)
        target_fats = round(weight * 0.9)
        rem_cals = target_calories - ((target_protein * 4) + (target_fats * 9))
        target_carbs = round(max(0, rem_cals / 4))
    else:
        target_protein = round(lean_mass_kg * 2.2)
        target_fats = round(weight * 0.8)
        rem_cals = target_calories - ((target_protein * 4) + (target_fats * 9))
        target_carbs = round(max(0, rem_cals / 4))
    
    return target_calories, target_protein, target_carbs, target_fats

# --- AI INTEGRATION ---
def analyze_food_with_gemini(food_input, note, api_key):
    if not api_key or "YOUR_API_KEY" in api_key:
        st.error("Please provide a valid API Key.")
        return None

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    
    prompt = f"""
    Analyze this food input: "{food_input}".
    Context note: "{note}" (use this to understand portion or type if vague).

    Estimate the nutritional content based on standard data.
    Return ONLY a raw JSON string with this structure:

    {{
        "food_name": "Short concise name",
        "calories": int,
        "protein": int,
        "carbs": int,
        "sugar": int,
        "fiber": int,
        "total_fats": int,
        "saturated_fat": int,
        "sodium": int,
        "vitamin_a": int,       # µg
        "vitamin_c": int,       # mg
        "vitamin_d": int,       # µg
        "calcium": int,         # mg
        "iron": int,            # mg
        "potassium": int,       # mg
        "magnesium": int,       # mg
        "zinc": int             # mg
    }}
    """
    try:
        response = model.generate_content(prompt)
        data = extract_json(response.text)
        if isinstance(data, list):
            return data[0] if len(data) > 0 else None
        return data
    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

def analyze_planned_meal(planned_food, rem_cals, rem_prot, cur_stats, api_key):
    if not api_key: return "API Key missing."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    
    prompt = f"""
    User plans to eat: "{planned_food}".
    Status: Remaining {rem_cals} Cals, {rem_prot}g Prot.
    Current Intake: Fiber {cur_stats['fiber']}g, Sugar {cur_stats['sugar']}g, Sodium {cur_stats['sodium']}mg.

    Analyze:
    1. Budget fit?
    2. Micro choice?
    3. Suggestions?
    Keep it concise.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Could not generate analysis: {e}"

def get_weekly_analysis(week_data, goal, api_key):
    if not api_key: return "API Key missing."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    prompt = f"""
    Analyze weekly nutrition data for goal "{goal}". Data: {week_data}.
    Provide: 1. Adherence summary. 2. Deficiency check. 3. One tip.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Could not generate analysis: {e}"

# --- ICONS & STYLING ---
def load_assets():
    # Load Material Symbols Rounded
    st.markdown("""
        <link rel="stylesheet" href="[https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0](https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0)" />
        <style>
            .icon {
                font-family: 'Material Symbols Rounded';
                font-weight: normal;
                font-style: normal;
                font-size: 24px;
                line-height: 1;
                letter-spacing: normal;
                text-transform: none;
                display: inline-block;
                white-space: nowrap;
                word-wrap: normal;
                direction: ltr;
                vertical-align: middle;
                margin-right: 5px;
            }
            .big-icon { font-size: 28px; }
            .metric-card {
                background-color: #f0f2f6;
                padding: 15px;
                border-radius: 10px;
                margin-bottom: 10px;
            }
            /* Custom Progress Bar for 'Bigger' look */
            .custom-bar-bg {
                background-color: #e0e0e0;
                border-radius: 8px;
                height: 20px;
                width: 100%;
                margin-top: 5px;
            }
            .custom-bar-fill {
                height: 100%;
                border-radius: 8px;
                transition: width 0.5s ease-in-out;
            }
        </style>
    """, unsafe_allow_html=True)

def render_big_metric(label, icon_name, value, target, unit, color):
    pct = min(value / target, 1.0) * 100 if target > 0 else 0
    st.markdown(f"""
        <div style="margin-bottom: 20px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px;">
                <span style="font-size: 1.2rem; font-weight: bold; color: #333;">
                    <span class="icon big-icon" style="color:{color}">{icon_name}</span> {label}
                </span>
                <span style="font-weight: bold; color: #555;">{value} / {target} {unit}</span>
            </div>
            <div class="custom-bar-bg">
                <div class="custom-bar-fill" style="width: {pct}%; background-color: {color};"></div>
            </div>
        </div>
    """, unsafe_allow_html=True)

def render_small_metric(label, icon_name, value, target, unit, color):
    pct = min(value / target, 1.0) * 100 if target > 0 else 0
    st.markdown(f"""
        <div style="margin-bottom: 10px;">
            <div style="display: flex; justify-content: space-between; font-size: 0.9rem;">
                <span><span class="icon" style="font-size: 18px; color:{color}">{icon_name}</span> {label}</span>
                <span>{value} / {target} {unit}</span>
            </div>
            <div class="custom-bar-bg" style="height: 8px;">
                <div class="custom-bar-fill" style="width: {pct}%; background-color: {color};"></div>
            </div>
        </div>
    """, unsafe_allow_html=True)

def render_micro_metric(label, icon_name, value, unit, color):
    st.markdown(f"""
        <div style="text-align: center; padding: 10px; background: #f8f9fa; border-radius: 8px;">
            <div class="icon" style="color:{color}; font-size: 24px; margin-bottom: 5px;">{icon_name}</div>
            <div style="font-size: 0.8rem; color: #666;">{label}</div>
            <div style="font-weight: bold; font-size: 1.0rem;">{value}{unit}</div>
        </div>
    """, unsafe_allow_html=True)

# --- MAIN APP ---
def main():
    load_assets() # Load Icons
    init_db()
    
    st.title("AI Body Recomposition Tracker")

    # --- SIDEBAR: CONFIG ---
    with st.sidebar:
        st.header("Settings")
        
        # API Key Input
        if API_KEY == "YOUR_API_KEY_HERE":
            user_api_key = st.text_input("Enter Gemini API Key", type="password")
            active_api_key = user_api_key
        else:
            active_api_key = API_KEY
            st.success("API Key loaded from secrets")

        # --- FETCH PROFILE DEFAULTS ---
        conn = get_db_connection()
        try:
            profile = conn.execute("SELECT height_cm, weight_kg, bf_percent, activity_level, goal, diet_type FROM users WHERE id=1").fetchone()
        except sqlite3.OperationalError:
             profile = None
        conn.close()
        
        p_h, p_w, p_bf = 175.0, 70.0, 20.0
        p_act, p_goal, p_diet = "Sedentary", "Maintain / Recomp", "Balanced"
        
        if profile:
            p_h = profile[0] if profile[0] else 175.0
            p_w = profile[1] if profile[1] else 70.0
            p_bf = profile[2] if profile[2] else 20.0
            p_act = profile[3] if profile[3] else "Sedentary"
            p_goal = profile[4] if profile[4] else "Maintain / Recomp"
            p_diet = profile[5] if profile[5] else "Balanced"

        st.divider()
        st.header("User Profile")
        with st.form("profile_form"):
            weight = st.number_input("Weight (kg)", value=float(p_w))
            height = st.number_input("Height (cm)", value=float(p_h))
            bf = st.number_input("Body Fat %", value=float(p_bf))
            
            act_opts = ["Sedentary", "Lightly Active", "Moderately Active", "Very Active"]
            activity = st.selectbox("Activity Level", act_opts, index=act_opts.index(p_act) if p_act in act_opts else 0)
            
            st.subheader("Goals")
            goal_opts = ["Maintain / Recomp", "Lose Weight", "Gain Muscle"]
            goal = st.selectbox("Primary Goal", goal_opts, index=goal_opts.index(p_goal) if p_goal in goal_opts else 0)
            
            diet_opts = ["Balanced", "High Protein", "Keto"]
            diet_type = st.selectbox("Diet Preference", diet_opts, index=diet_opts.index(p_diet) if p_diet in diet_opts else 0)
            
            if st.form_submit_button("Update Targets"):
                cals, prot, carbs, fats = calculate_macros(weight, height, bf, activity, goal, diet_type)
                conn = get_db_connection()
                conn.execute("""INSERT OR REPLACE INTO users 
                                (id, height_cm, weight_kg, bf_percent, activity_level, goal, diet_type,
                                 target_calories, target_protein, target_carbs, target_fats) 
                                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", 
                             (height, weight, bf, activity, goal, diet_type, cals, prot, carbs, fats))
                conn.commit()
                conn.close()
                st.toast("Targets Updated!")
                st.rerun()

        # --- LOG BODY STATS ---
        st.divider()
        st.header("Log Body Stats")
        
        conn = get_db_connection()
        try:
            last_stat = conn.execute("SELECT weight_kg, bf_percent FROM body_stats ORDER BY date DESC LIMIT 1").fetchone()
        except:
            last_stat = None
        conn.close()
        
        last_w = last_stat[0] if last_stat else p_w
        last_bf = last_stat[1] if last_stat else p_bf

        with st.expander("Record Weigh-in"):
            with st.form("weight_log"):
                log_date = st.date_input("Date", value=datetime.now())
                log_weight = st.number_input("Current Weight (kg)", value=float(last_w))
                log_bf = st.number_input("Current BF % (optional)", value=float(last_bf))
                
                if st.form_submit_button("Log Stats"):
                    conn = get_db_connection()
                    conn.execute("INSERT INTO body_stats (date, weight_kg, bf_percent) VALUES (?, ?, ?)",
                                 (log_date.strftime("%Y-%m-%d"), log_weight, log_bf))
                    conn.commit()
                    conn.close()
                    st.success("Body stats logged.")
                    st.rerun()

    # --- LOAD USER DATA ---
    conn = get_db_connection()
    try:
        user_data = conn.execute("SELECT target_calories, target_protein, target_carbs, target_fats, goal FROM users WHERE id=1").fetchone()
    except:
        user_data = None
    conn.close()

    if not user_data:
        st.info("👈 Please set your profile in the sidebar to begin.")
        return
        
    base_cals, t_prot, t_carbs, t_fats, user_goal = user_data
    if user_goal is None: user_goal = "Maintain / Recomp"

    # --- DATE HANDLING ---
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    conn = get_db_connection()
    try:
        y_stats = conn.execute("SELECT SUM(calories) FROM food_logs WHERE date = ?", (yesterday,)).fetchone()
    except:
        y_stats = None
    conn.close()
    
    y_cals = y_stats[0] if y_stats and y_stats[0] else 0
    daily_target_cals = base_cals
    
    if "Maintain" in user_goal and y_cals > base_cals:
        overage = y_cals - base_cals
        deduction = min(overage, base_cals * 0.15)
        daily_target_cals -= deduction
        st.warning(f"📉 Adaptive Adjustment: Target reduced by {deduction:.0f} kcal")

    # --- TABS UI ---
    tab1, tab2 = st.tabs(["Daily Tracker", "AI Coach"])

    # --- TAB 1: TRACKER ---
    with tab1:
        c_date, c_spacer = st.columns([1, 4])
        with c_date:
            view_date_obj = st.date_input("Tracking Date", value=datetime.now())
            view_date = view_date_obj.strftime("%Y-%m-%d")

        col1, col2 = st.columns([1.6, 1])
        
        current_target_cals = daily_target_cals if view_date == today else base_cals

        with col1:
            st.subheader("Daily Overview")
            
            # Fetch Data
            conn = get_db_connection()
            stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(carbs), SUM(fats),
                                    SUM(fiber), SUM(sugar), SUM(sodium), SUM(saturated_fat)
                                    FROM food_logs WHERE date = ?""", (view_date,)).fetchone()
            conn.close()
            
            c_cal = stats[0] or 0
            c_prot = stats[1] or 0
            c_carb = stats[2] or 0
            c_fat = stats[3] or 0
            c_fiber = stats[4] or 0
            c_sugar = stats[5] or 0
            c_sodium = stats[6] or 0
            c_sat_fat = stats[7] or 0
            
            # --- TIER 1: CALORIES & PROTEIN (Primary) ---
            t1_c1, t1_c2 = st.columns(2)
            with t1_c1:
                render_big_metric("Calories", "local_fire_department", c_cal, current_target_cals, "kcal", "#ff5722")
            with t1_c2:
                render_big_metric("Protein", "fitness_center", c_prot, t_prot, "g", "#4caf50")
                
            # --- TIER 2: OTHER MACROS (Secondary) ---
            t2_c1, t2_c2 = st.columns(2)
            with t2_c1:
                render_small_metric("Carbs", "bakery_dining", c_carb, t_carbs, "g", "#2196f3")
                render_small_metric("Fiber", "grass", c_fiber, 30, "g", "#8bc34a") # Avg target 30g
                render_small_metric("Sugar", "icecream", c_sugar, 50, "g", "#e91e63") # Avg limit 50g
            with t2_c2:
                render_small_metric("Fats", "opacity", c_fat, t_fats, "g", "#ffc107")
                render_small_metric("Sat. Fat", "water_drop", c_sat_fat, 20, "g", "#fbc02d") # Avg limit 20g
                render_small_metric("Sodium", "grain", c_sodium, 2300, "mg", "#9e9e9e")

            # --- TIER 3: MICRONUTRIENTS ---
            st.write("") # Spacer
            st.markdown("**Micronutrients**")
            
            # Fetch Micros Sum
            conn = get_db_connection()
            micro_stats = conn.execute("""SELECT SUM(vitamin_a), SUM(vitamin_c), SUM(vitamin_d), 
                                          SUM(calcium), SUM(iron), SUM(potassium), SUM(magnesium), SUM(zinc)
                                          FROM food_logs WHERE date = ?""", (view_date,)).fetchone()
            conn.close()
            
            # Unpack safely
            c_vit_a = micro_stats[0] or 0
            c_vit_c = micro_stats[1] or 0
            c_vit_d = micro_stats[2] or 0
            c_calc = micro_stats[3] or 0
            c_iron = micro_stats[4] or 0
            c_pot = micro_stats[5] or 0
            c_mag = micro_stats[6] or 0
            c_zinc = micro_stats[7] or 0

            m_row1_1, m_row1_2, m_row1_3, m_row1_4 = st.columns(4)
            with m_row1_1: render_micro_metric("Vit A", "visibility", c_vit_a, "µg", "#FF9800")
            with m_row1_2: render_micro_metric("Vit C", "nutrition", c_vit_c, "mg", "#FFEB3B")
            with m_row1_3: render_micro_metric("Vit D", "sunny", c_vit_d, "µg", "#FFC107")
            with m_row1_4: render_micro_metric("Calcium", "egg", c_calc, "mg", "#F5F5F5") # White-ish

            m_row2_1, m_row2_2, m_row2_3, m_row2_4 = st.columns(4)
            with m_row2_1: render_micro_metric("Iron", "hexagon", c_iron, "mg", "#795548")
            with m_row2_2: render_micro_metric("Potass.", "bolt", c_pot, "mg", "#673AB7")
            with m_row2_3: render_micro_metric("Magnes.", "spa", c_mag, "mg", "#009688")
            with m_row2_4: render_micro_metric("Zinc", "science", c_zinc, "mg", "#607D8B")

            st.divider()
            
            # --- MEAL LOGGING ---
            with st.container(border=True):
                st.markdown(f"#### <span class='icon'>add_circle</span> Add Meal", unsafe_allow_html=True)
                f_name = st.text_input("Describe your meal", placeholder="e.g., Double cheeseburger no bun")
                f_note = st.text_input("Note (Optional)", placeholder="e.g., Ate out, Snack at work")
                
                if st.button("Log Meal", type="primary"):
                    if not f_name:
                        st.warning("Please describe your food first.")
                    else:
                        with st.spinner("Analyzing..."):
                            data = analyze_food_with_gemini(f_name, f_note, active_api_key)
                            if data:
                                calories = int(data.get('calories', 0) or 0)
                                protein = int(data.get('protein', 0) or 0)
                                if protein == 0 and calories > 0:
                                    protein = max(1, round(calories * 0.2 / 4))
                                
                                conn = get_db_connection()
                                conn.execute("""INSERT INTO food_logs 
                                    (date, food_name, amount_desc, calories, protein, carbs, fats, fiber, sugar, sodium, saturated_fat,
                                     vitamin_a, vitamin_c, vitamin_d, calcium, iron, potassium, magnesium, zinc, note) 
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (view_date, 
                                     data.get('food_name', 'Unknown Food'), 
                                     f_name, 
                                     calories, protein, 
                                     int(data.get('carbs', 0) or 0), 
                                     int(data.get('total_fats', 0) or 0), 
                                     int(data.get('fiber', 0) or 0), 
                                     int(data.get('sugar', 0) or 0), 
                                     int(data.get('sodium', 0) or 0), 
                                     int(data.get('saturated_fat', 0) or 0),
                                     int(data.get('vitamin_a', 0) or 0),
                                     int(data.get('vitamin_c', 0) or 0),
                                     int(data.get('vitamin_d', 0) or 0),
                                     int(data.get('calcium', 0) or 0),
                                     int(data.get('iron', 0) or 0),
                                     int(data.get('potassium', 0) or 0),
                                     int(data.get('magnesium', 0) or 0),
                                     int(data.get('zinc', 0) or 0),
                                     f_note))
                                conn.commit()
                                conn.close()
                                st.rerun()
                            else:
                                st.error("Analysis failed.")

        with col2:
            st.subheader("Logs")
            
            conn = get_db_connection()
            logs = conn.execute("""
                SELECT id, food_name, calories, protein, carbs, fats, fiber, sugar, sodium, saturated_fat, note 
                FROM food_logs WHERE date = ? ORDER BY id DESC
            """, (view_date,)).fetchall()
            conn.close()
            
            if not logs:
                st.info(f"No meals logged.")
            else:
                for log in logs:
                    log_id, name, cal, prot, carb, fat, fib, sug, sod, sat_fat, note = log
                    fib = fib or 0
                    sug = sug or 0
                    sod = sod or 0
                    sat_fat = sat_fat or 0
                    
                    with st.container(border=True):
                        # Header
                        row1_col1, row1_col2 = st.columns([5, 1])
                        with row1_col1: st.markdown(f"**{name}**")
                        with row1_col2:
                            if st.button("✖", key=f"del_{log_id}"):
                                conn = get_db_connection()
                                conn.execute("DELETE FROM food_logs WHERE id = ?", (log_id,))
                                conn.commit()
                                conn.close()
                                st.rerun()

                        # Main Stats (Big)
                        st.markdown(f"""
                        <div style='display:flex; gap:15px; align-items:center; margin-bottom:5px;'>
                            <span style='color:#4caf50; font-weight:bold;'><span class='icon'>fitness_center</span>{prot}g</span>
                            <span style='color:#ff5722; font-weight:bold;'><span class='icon'>local_fire_department</span>{cal}</span>
                        </div>
                        """, unsafe_allow_html=True)

                        # Other Macros (Small)
                        st.markdown(f"""
                        <div style='font-size: 0.85rem; color: #555;'>
                            C:{carb}g F:{fat}g (Sat:{sat_fat}g) Fib:{fib}g Sug:{sug}g Sod:{sod}mg
                        </div>
                        """, unsafe_allow_html=True)
                        
                        if note: st.caption(f"📝 {note}")
                
                if st.button("Clear Logs", type="secondary"):
                    conn = get_db_connection()
                    conn.execute("DELETE FROM food_logs WHERE date = ?", (view_date,))
                    conn.commit()
                    conn.close()
                    st.rerun()

    # --- TAB 2: AI COACH ---
    with tab2:
        st.subheader("AI Coach")
        
        conn = get_db_connection()
        today_stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(fiber), SUM(sugar), SUM(sodium) 
                                      FROM food_logs WHERE date = ?""", (today,)).fetchone()
        
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_logs = pd.read_sql_query(f"SELECT * FROM food_logs WHERE date >= '{week_ago}'", conn)
        
        # Consistency Data
        try:
            df_food = pd.read_sql_query("SELECT date, SUM(calories) as calories, SUM(protein) as protein FROM food_logs GROUP BY date ORDER BY date", conn)
        except:
            df_food = pd.DataFrame()
        conn.close()

        t_cur_cal = today_stats[0] or 0
        t_cur_prot = today_stats[1] or 0
        rem_cals = daily_target_cals - t_cur_cal
        rem_prot = t_prot - t_cur_prot

        # Planned Meal
        with st.container(border=True):
            st.markdown(f"#### <span class='icon'>psychology</span> Analyze Plan", unsafe_allow_html=True)
            st.info(f"Remaining: {rem_cals:.0f} cal, {rem_prot}g prot")
            
            c1, c2 = st.columns([3,1])
            with c1: planned_food = st.text_input("Planned food", placeholder="Pizza slice...")
            with c2: 
                st.write("")
                st.write("")
                if st.button("Analyze"):
                    if planned_food:
                        with st.spinner("Thinking..."):
                            cur = {'fiber': today_stats[2] or 0, 'sugar': today_stats[3] or 0, 'sodium': today_stats[4] or 0}
                            res = analyze_planned_meal(planned_food, int(rem_cals), int(rem_prot), cur, active_api_key)
                            st.markdown(res)

        st.divider()

        # Consistency
        st.markdown("#### Consistency")
        if not df_food.empty:
            df_food['hit_prot'] = df_food['protein'] >= (t_prot * 0.9)
            df_food['hit_cal'] = (df_food['calories'] >= (base_cals * 0.9)) & (df_food['calories'] <= (base_cals * 1.1))
            
            c1, c2 = st.columns(2)
            prot_rate = (df_food['hit_prot'].sum() / len(df_food)) * 100
            cal_rate = (df_food['hit_cal'].sum() / len(df_food)) * 100
            
            c1.metric("Protein Rate", f"{prot_rate:.0f}%")
            c2.metric("Calorie Rate", f"{cal_rate:.0f}%")
        else:
            st.info("Log meals to track consistency.")

        st.divider()

        # Weekly Report
        c1, c2 = st.columns([3,1])
        with c1: st.markdown("#### Weekly Report")
        with c2:
            if not week_logs.empty and st.button("Generate"):
                with st.spinner("Generating..."):
                    summary = week_logs.groupby('date')[['calories', 'protein', 'fiber']].sum().to_string()
                    rep = get_weekly_analysis(summary, user_goal, active_api_key)
                    st.markdown(rep)
        
        if not week_logs.empty:
             st.dataframe(week_logs[['date', 'food_name', 'calories', 'protein']].head(5), hide_index=True)

if __name__ == "__main__":
    main()
