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

    # Food logs table
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
    
    # Migration for food_logs
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
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    prompt = f"""
    You are a nutritionist AI. Analyze the following food input and estimate its nutritional content
    based on typical serving sizes and standard data.

    Food description: "{food_input}"
    Context note: "{note}" (use this to understand portion or type if vague).

    Return ONLY a JSON with this structure:

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

    Provide reasonable estimates for all fields.
    """
    
    try:
        response = model.generate_content(prompt)
        data = extract_json(response.text)
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        return data
    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

def analyze_planned_meal(planned_food, current_status, targets, api_key):
    if not api_key: return "API Key missing."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    
    prompt = f"""
    You are a nutrition coach.
    User plans to eat: "{planned_food}".

    **Daily Targets:**
    Calories: {targets['cals']}, Protein: {targets['prot']}g, Carbs: {targets['carbs']}g, Fats: {targets['fats']}g.

    **Current Intake Today:**
    Calories: {current_status['cals']}, Protein: {current_status['prot']}g.
    Micros: Fiber {current_status['fiber']}g, Sugar {current_status['sugar']}g, Sodium {current_status['sodium']}mg.

    **Task:**
    1. Analyze if this food fits within the remaining budget.
    2. Check if the user is hitting their macro/micro goals for today.
    3. Provide specific suggestions (e.g., "Add more fiber", "Watch the sodium", "Perfect fit").
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Could not generate analysis: {e}"

def get_weekly_analysis(week_data, averages, targets, goal, api_key):
    if not api_key: return "API Key missing."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    prompt = f"""
    Analyze weekly nutrition data for goal "{goal}".
    
    **Weekly Averages vs Targets:**
    Calories: {averages['cals']} (Target: {targets['cals']})
    Protein: {averages['prot']}g (Target: {targets['prot']}g)
    Carbs: {averages['carbs']}g (Target: {targets['carbs']}g)
    Fats: {averages['fats']}g (Target: {targets['fats']}g)

    **Detailed Log Data:**
    {week_data}
    
    Provide:
    1. A summary of adherence to the goal.
    2. Identify specific wins and areas for improvement (e.g., "Consistently under protein").
    3. One actionable tip for next week.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Could not generate analysis: {e}"

# --- ICONS & STYLING ---
def load_assets():
    st.markdown("""
        <link rel="stylesheet" href="[https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0](https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0)" />
        <style>
            .icon {
                font-family: 'Material Symbols Rounded';
                font-size: 24px;
                vertical-align: middle;
            }
            .big-icon { font-size: 28px; }
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
    load_assets()
    init_db()
    
    st.title("AI Body Recomposition Tracker")

    # --- SIDEBAR ---
    with st.sidebar:
        st.header("Settings")
        if API_KEY == "YOUR_API_KEY_HERE":
            active_api_key = st.text_input("Enter Gemini API Key", type="password")
        else:
            active_api_key = API_KEY
            st.success("API Key loaded")

        conn = get_db_connection()
        try:
            profile = conn.execute("SELECT height_cm, weight_kg, bf_percent, activity_level, goal, diet_type FROM users WHERE id=1").fetchone()
        except: profile = None
        conn.close()
        
        p_h, p_w, p_bf = 175.0, 70.0, 20.0
        p_act, p_goal, p_diet = "Sedentary", "Maintain / Recomp", "Balanced"
        
        if profile:
            p_h, p_w, p_bf = profile[0] or 175.0, profile[1] or 70.0, profile[2] or 20.0
            p_act, p_goal, p_diet = profile[3] or "Sedentary", profile[4] or "Maintain / Recomp", profile[5] or "Balanced"

        st.divider()
        st.header("User Profile")
        with st.form("profile_form"):
            weight = st.number_input("Weight (kg)", value=float(p_w))
            height = st.number_input("Height (cm)", value=float(p_h))
            bf = st.number_input("Body Fat %", value=float(p_bf))
            
            act_opts = ["Sedentary", "Lightly Active", "Moderately Active", "Very Active"]
            activity = st.selectbox("Activity Level", act_opts, index=act_opts.index(p_act) if p_act in act_opts else 0)
            
            goal_opts = ["Maintain / Recomp", "Lose Weight", "Gain Muscle"]
            goal = st.selectbox("Primary Goal", goal_opts, index=goal_opts.index(p_goal) if p_goal in goal_opts else 0)
            
            diet_opts = ["Balanced", "High Protein", "Keto"]
            diet_type = st.selectbox("Diet Preference", diet_opts, index=diet_opts.index(p_diet) if p_diet in diet_opts else 0)
            
            if st.form_submit_button("Update Targets"):
                cals, prot, carbs, fats = calculate_macros(weight, height, bf, activity, goal, diet_type)
                conn = get_db_connection()
                conn.execute("""INSERT OR REPLACE INTO users (id, height_cm, weight_kg, bf_percent, activity_level, goal, diet_type, target_calories, target_protein, target_carbs, target_fats) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (height, weight, bf, activity, goal, diet_type, cals, prot, carbs, fats))
                conn.commit()
                conn.close()
                st.rerun()

    # --- DATA & CALCULATIONS ---
    conn = get_db_connection()
    try:
        user_data = conn.execute("SELECT target_calories, target_protein, target_carbs, target_fats, goal FROM users WHERE id=1").fetchone()
    except: user_data = None
    conn.close()

    if not user_data:
        st.info("Please set profile.")
        return
        
    base_cals, t_prot, t_carbs, t_fats, user_goal = user_data
    if user_goal is None: user_goal = "Maintain / Recomp"

    today = datetime.now().strftime("%Y-%m-%d")
    daily_target_cals = base_cals # Simplified for brevity

    # --- TABS ---
    tab1, tab2 = st.tabs(["Daily Tracker", "AI Coach"])

    # --- TAB 1: DAILY TRACKER ---
    with tab1:
        c_date, _ = st.columns([1, 4])
        with c_date:
            view_date_obj = st.date_input("Tracking Date", value=datetime.now())
            view_date = view_date_obj.strftime("%Y-%m-%d")

        col1, col2 = st.columns([1.6, 1])
        
        with col1:
            st.subheader("Daily Overview")
            conn = get_db_connection()
            stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(carbs), SUM(fats), SUM(fiber), SUM(sugar), SUM(sodium), SUM(saturated_fat) FROM food_logs WHERE date = ?""", (view_date,)).fetchone()
            conn.close()
            
            c_cal, c_prot = stats[0] or 0, stats[1] or 0
            c_carb, c_fat = stats[2] or 0, stats[3] or 0
            c_fiber, c_sugar, c_sodium, c_sat_fat = stats[4] or 0, stats[5] or 0, stats[6] or 0, stats[7] or 0
            
            t1_c1, t1_c2 = st.columns(2)
            with t1_c1: render_big_metric("Calories", "local_fire_department", c_cal, daily_target_cals, "kcal", "#ff5722")
            with t1_c2: render_big_metric("Protein", "fitness_center", c_prot, t_prot, "g", "#4caf50")
                
            t2_c1, t2_c2 = st.columns(2)
            with t2_c1:
                render_small_metric("Carbs", "bakery_dining", c_carb, t_carbs, "g", "#2196f3")
                render_small_metric("Fiber", "grass", c_fiber, 30, "g", "#8bc34a")
                render_small_metric("Sugar", "icecream", c_sugar, 50, "g", "#e91e63")
            with t2_c2:
                render_small_metric("Fats", "opacity", c_fat, t_fats, "g", "#ffc107")
                render_small_metric("Sat. Fat", "water_drop", c_sat_fat, 20, "g", "#fbc02d")
                render_small_metric("Sodium", "grain", c_sodium, 2300, "mg", "#9e9e9e")

            st.write("")
            st.markdown("**Micronutrients**")
            conn = get_db_connection()
            m_stats = conn.execute("""SELECT SUM(vitamin_a), SUM(vitamin_c), SUM(vitamin_d), SUM(calcium), SUM(iron), SUM(potassium), SUM(magnesium), SUM(zinc) FROM food_logs WHERE date = ?""", (view_date,)).fetchone()
            conn.close()
            
            m1, m2, m3, m4 = st.columns(4)
            with m1: render_micro_metric("Vit A", "visibility", m_stats[0] or 0, "µg", "#FF9800")
            with m2: render_micro_metric("Vit C", "nutrition", m_stats[1] or 0, "mg", "#FFEB3B")
            with m3: render_micro_metric("Vit D", "sunny", m_stats[2] or 0, "µg", "#FFC107")
            with m4: render_micro_metric("Calc.", "egg", m_stats[3] or 0, "mg", "#F5F5F5")

            m5, m6, m7, m8 = st.columns(4)
            with m5: render_micro_metric("Iron", "hexagon", m_stats[4] or 0, "mg", "#795548")
            with m6: render_micro_metric("Potass.", "bolt", m_stats[5] or 0, "mg", "#673AB7")
            with m7: render_micro_metric("Magnes.", "spa", m_stats[6] or 0, "mg", "#009688")
            with m8: render_micro_metric("Zinc", "science", m_stats[7] or 0, "mg", "#607D8B")

            st.divider()
            with st.container(border=True):
                st.markdown(f"#### <span class='icon'>add_circle</span> Add Meal", unsafe_allow_html=True)
                f_name = st.text_input("Describe your meal", placeholder="e.g., Double cheeseburger no bun")
                f_note = st.text_input("Note (Optional)", placeholder="e.g., Ate out, Snack at work")
                if st.button("Log Meal", type="primary"):
                    if not f_name: st.warning("Describe food first.")
                    else:
                        with st.spinner("Analyzing..."):
                            data = analyze_food_with_gemini(f_name, f_note, active_api_key)
                            if data:
                                conn = get_db_connection()
                                conn.execute("""INSERT INTO food_logs (date, food_name, amount_desc, calories, protein, carbs, sugar, fiber, fats, saturated_fat, sodium, vitamin_a, vitamin_c, vitamin_d, calcium, iron, potassium, magnesium, zinc, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (view_date, data['food_name'], f_name, data['calories'], data['protein'], data['carbs'], data['sugar'], data['fiber'], data['total_fats'], data['saturated_fat'], data['sodium'], data['vitamin_a'], data['vitamin_c'], data['vitamin_d'], data['calcium'], data['iron'], data['potassium'], data['magnesium'], data['zinc'], f_note))
                                conn.commit()
                                conn.close()
                                st.rerun()
                            else: st.error("Analysis failed.")

        with col2:
            st.subheader("Logs")
            conn = get_db_connection()
            logs = conn.execute("""SELECT id, food_name, calories, protein, carbs, fats, fiber, sugar, sodium, saturated_fat, note FROM food_logs WHERE date = ? ORDER BY id DESC""", (view_date,)).fetchall()
            conn.close()
            if logs:
                for log in logs:
                    with st.container(border=True):
                        c1, c2 = st.columns([5,1])
                        with c1: st.markdown(f"**{log[1]}**")
                        with c2: 
                            if st.button("✖", key=f"d_{log[0]}"):
                                conn = get_db_connection()
                                conn.execute("DELETE FROM food_logs WHERE id=?", (log[0],))
                                conn.commit(); conn.close(); st.rerun()
                        st.markdown(f"""
                        <div style='display:flex; gap:20px; margin:10px 0;'>
                            <span style='color:#4caf50; font-weight:bold; font-size: 1.1em;'><span class='icon'>fitness_center</span>{log[3]}g</span>
                            <span style='color:#ff5722; font-weight:bold; font-size: 1.1em;'><span class='icon'>local_fire_department</span>{log[2]}</span>
                        </div>
                        <div style='font-size:0.85em; color:#555;'>C:{log[4]}g F:{log[5]}g (Sat:{log[9]}g) Fib:{log[6]}g Sug:{log[7]}g Sod:{log[8]}mg</div>
                        """, unsafe_allow_html=True)
            else: st.info("No meals.")

    # --- TAB 2: AI COACH ---
    with tab2:
        st.markdown("### <span class='icon'>smart_toy</span> AI Nutrition Coach", unsafe_allow_html=True)
        
        # 1. TOP: MEAL ANALYSIS & CURRENT STATUS
        conn = get_db_connection()
        today_stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(fiber), SUM(sugar), SUM(sodium) FROM food_logs WHERE date = ?""", (today,)).fetchone()
        
        # Get All History for Consistency
        df_all = pd.read_sql_query("SELECT date, SUM(calories) as calories, SUM(protein) as protein, SUM(carbs) as carbs, SUM(fats) as fats FROM food_logs GROUP BY date ORDER BY date", conn)
        
        # Get Weekly Data
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_logs = pd.read_sql_query(f"SELECT * FROM food_logs WHERE date >= '{week_ago}'", conn)
        conn.close()

        # Current Status Dictionary
        cur_status = {
            'cals': today_stats[0] or 0,
            'prot': today_stats[1] or 0,
            'fiber': today_stats[2] or 0,
            'sugar': today_stats[3] or 0,
            'sodium': today_stats[4] or 0
        }
        targets = {'cals': daily_target_cals, 'prot': t_prot, 'carbs': t_carbs, 'fats': t_fats}

        with st.container(border=True):
            st.markdown("#### <span class='icon'>psychology_alt</span> Analyze Planned Meal", unsafe_allow_html=True)
            st.markdown(f"**Current Status:** {cur_status['cals']}/{daily_target_cals} Cals • {cur_status['prot']}/{t_prot}g Protein")
            
            c_input, c_btn = st.columns([3, 1])
            with c_input: planned = st.text_input("What are you planning to eat?", placeholder="e.g. Chicken breast and rice")
            with c_btn: 
                st.write("")
                st.write("")
                ask_coach = st.button("Ask Coach", type="primary")
                
            if ask_coach:
                if planned:
                    with st.spinner("Analyzing fit..."):
                        advice = analyze_planned_meal(planned, cur_status, targets, active_api_key)
                        st.markdown(advice)

        st.divider()

        # 2. MIDDLE: CONSISTENCY TRACKER
        st.markdown("#### <span class='icon'>trophy</span> Consistency Tracker (All Time)", unsafe_allow_html=True)
        
        if not df_all.empty:
            avg_cals = df_all['calories'].mean()
            avg_prot = df_all['protein'].mean()
            avg_carbs = df_all['carbs'].mean()
            avg_fats = df_all['fats'].mean()
            
            # Display Averages vs Targets
            col_a, col_b, col_c, col_d = st.columns(4)
            
            def diff_metric(col, label, val, target, unit):
                diff = val - target
                delta_str = f"{diff:+.0f} {unit}"
                col.metric(label, f"{val:.0f} {unit}", delta_str, delta_color="inverse" if label=="Calories" or label=="Carbs" or label=="Fats" else "normal")

            diff_metric(col_a, "Avg Calories", avg_cals, daily_target_cals, "")
            diff_metric(col_b, "Avg Protein", avg_prot, t_prot, "g")
            diff_metric(col_c, "Avg Carbs", avg_carbs, t_carbs, "g")
            diff_metric(col_d, "Avg Fats", avg_fats, t_fats, "g")
            
            # Hit Rate
            df_all['hit_prot'] = df_all['protein'] >= (t_prot * 0.9)
            hit_rate = (df_all['hit_prot'].sum() / len(df_all)) * 100
            st.caption(f"You hit your protein goal **{hit_rate:.1f}%** of the days logged.")
            
        else:
            st.info("Log more meals to see your consistency stats.")

        st.divider()

        # 3. BOTTOM: WEEKLY REPORT & WINDOWS
        st.markdown("#### <span class='icon'>calendar_month</span> Weekly Report", unsafe_allow_html=True)
        
        if not week_logs.empty:
            # Calculate Weekly Averages
            week_grouped = week_logs.groupby('date')[['calories', 'protein', 'carbs', 'fats']].sum()
            w_avg_cals = week_grouped['calories'].mean()
            w_avg_prot = week_grouped['protein'].mean()
            w_avg_carbs = week_grouped['carbs'].mean()
            w_avg_fats = week_grouped['fats'].mean()
            
            # Generate AI Report Button
            if st.button("Generate Weekly Analysis"):
                with st.spinner("Coach is reviewing your week..."):
                    avgs = {'cals': int(w_avg_cals), 'prot': int(w_avg_prot), 'carbs': int(w_avg_carbs), 'fats': int(w_avg_fats)}
                    summary_text = week_grouped.to_string()
                    report = get_weekly_analysis(summary_text, avgs, targets, user_goal, active_api_key)
                    st.markdown(report)
            
            st.write("")
            st.markdown("##### Weekly Averages Summary")
            
            # Small Windows (Cards) at the bottom
            w1, w2, w3, w4 = st.columns(4)
            
            def render_window(col, title, val, target, unit):
                status = "<span class='icon' style='color:green'>check_circle</span> On Track"
                if val > target * 1.1: status = "<span class='icon' style='color:red'>error</span> Over"
                elif val < target * 0.9: status = "<span class='icon' style='color:orange'>warning</span> Under"
                
                with col:
                    with st.container(border=True):
                        st.markdown(f"**{title}**")
                        st.markdown(f"Avg: {val:.0f}{unit}")
                        st.caption(f"Target: {target}{unit}")
                        st.markdown(f"**{status}**", unsafe_allow_html=True)

            render_window(w1, "Calories", w_avg_cals, daily_target_cals, "")
            render_window(w2, "Protein", w_avg_prot, t_prot, "g")
            render_window(w3, "Carbs", w_avg_carbs, t_carbs, "g")
            render_window(w4, "Fats", w_avg_fats, t_fats, "g")
            
        else:
            st.info("No data logged for the past 7 days.")

if __name__ == "__main__":
    main()
