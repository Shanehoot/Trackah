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
    
    # Users table - Updated for new preferences
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, height_cm REAL, weight_kg REAL, 
                  bf_percent REAL, activity_level TEXT, 
                  goal TEXT, diet_type TEXT,
                  target_calories REAL, target_protein REAL, 
                  target_carbs REAL, target_fats REAL)''')
    
    # Migration for users table if goal/diet_type missing
    try:
        c.execute("ALTER TABLE users ADD COLUMN goal TEXT")
        c.execute("ALTER TABLE users ADD COLUMN diet_type TEXT")
    except sqlite3.OperationalError:
        pass

    # Food logs table - Updated for micro nutrients and notes
    c.execute('''CREATE TABLE IF NOT EXISTS food_logs 
                 (id INTEGER PRIMARY KEY, date TEXT, food_name TEXT, 
                  amount_desc TEXT, calories INTEGER, 
                  protein INTEGER, carbs INTEGER, fats INTEGER, 
                  fiber INTEGER, sugar INTEGER, sodium INTEGER,
                  nutrients TEXT, note TEXT)''')
    
    # Migration for food_logs
    columns_to_add = ["fiber", "sugar", "sodium", "note"]
    for col in columns_to_add:
        try:
            # Type handling for alteration is simple in sqlite
            col_type = "TEXT" if col == "note" else "INTEGER"
            c.execute(f"ALTER TABLE food_logs ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass

    # Body Stats table - NEW
    c.execute('''CREATE TABLE IF NOT EXISTS body_stats 
                 (id INTEGER PRIMARY KEY, date TEXT, weight_kg REAL, bf_percent REAL)''')
    
    conn.commit()
    conn.close()

# --- UTILITIES ---
def extract_json(text):
    """
    Robustly extracts JSON from AI response.
    """
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
    
    # 1. Adjust for Goal
    if goal == "Lose Weight":
        target_calories = round(tdee - 500) # Standard deficit
    elif goal == "Gain Muscle":
        target_calories = round(tdee + 300) # Lean bulk surplus
    else: # Maintenance / Recomp
        target_calories = round(tdee)
    
    # 2. Adjust for Diet Type (Macro Split)
    if diet_type == "Keto":
        target_carbs = 30 # Hard cap
        target_protein = round(lean_mass_kg * 2.0)
        # Remainder fats
        rem_cals = target_calories - ((target_protein * 4) + (target_carbs * 4))
        target_fats = round(max(0, rem_cals / 9))
        
    elif diet_type == "High Protein":
        target_protein = round(lean_mass_kg * 2.6) # Very high
        target_fats = round(weight * 0.9)
        rem_cals = target_calories - ((target_protein * 4) + (target_fats * 9))
        target_carbs = round(max(0, rem_cals / 4))
        
    else: # Balanced / Standard Recomp
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
    # Switched to preview model for compatibility
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    
    prompt = f"""
    Analyze this food input: "{food_input}".
    Context note: "{note}" (use this to understand portion or type if vague).
    
    Estimate values based on standard data.
    Return ONLY a raw JSON string with this structure:
    {{
        "food_name": "Short concise name",
        "calories": int,
        "protein": int,
        "carbs": int,
        "fats": int,
        "fiber": int,
        "sugar": int,
        "sodium": int,
        "micronutrients": "3 key vitamins/minerals"
    }}
    """
    try:
        response = model.generate_content(prompt)
        data = extract_json(response.text)
        
        # SAFEGUARD: Handle case where AI returns a list [{}, {}] instead of a single dict {}
        if isinstance(data, list):
            if len(data) > 0:
                return data[0]
            else:
                return None
                
        return data
    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

def analyze_planned_meal(planned_food, rem_cals, rem_prot, cur_stats, api_key):
    """
    New function to analyze a planned meal against remaining budgets and current micro intake.
    """
    if not api_key: return "API Key missing."
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    
    prompt = f"""
    You are a helpful nutrition coach. The user is planning to eat: "{planned_food}".

    Here is their current status for the day:
    - **Remaining Budget:** {rem_cals} Calories, {rem_prot}g Protein.
    - **Current Intake:** Fiber: {cur_stats['fiber']}g, Sugar: {cur_stats['sugar']}g, Sodium: {cur_stats['sodium']}mg.

    Please analyze this plan:
    1. Will this meal fit their remaining calorie/protein budget?
    2. Consider the fiber, sugar, and sodium. Is this meal a good choice given what they've already eaten?
    3. If it's not ideal, suggest a modification or a specific portion size.
    
    Keep the response concise, encouraging, and actionable.
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
    
    # week_data is a dictionary or string summary
    prompt = f"""
    You are a fitness coach. Analyze this weekly nutrition data for a user whose goal is "{goal}".
    
    Data:
    {week_data}
    
    Provide:
    1. A brief summary of adherence.
    2. Highlight any specific nutrient deficiencies (Fiber, Sugar spikes, etc).
    3. One actionable tip for next week.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Could not generate analysis: {e}"

# --- MAIN APP ---
def main():
    init_db()
    
    st.title("🧬 AI Body Recomposition Tracker")

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
        
        # Default values if no profile exists
        p_h, p_w, p_bf = 175.0, 70.0, 20.0
        p_act, p_goal, p_diet = "Sedentary", "Maintain / Recomp", "Balanced"
        
        if profile:
            # Handle potential None values if migration just happened
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
            
            # Helper to find index safely
            def get_index(options, val):
                try: return options.index(val)
                except: return 0
                
            act_opts = ["Sedentary", "Lightly Active", "Moderately Active", "Very Active"]
            activity = st.selectbox("Activity Level", act_opts, index=get_index(act_opts, p_act))
            
            st.subheader("Goals")
            goal_opts = ["Maintain / Recomp", "Lose Weight", "Gain Muscle"]
            goal = st.selectbox("Primary Goal", goal_opts, index=get_index(goal_opts, p_goal))
            
            diet_opts = ["Balanced", "High Protein", "Keto"]
            diet_type = st.selectbox("Diet Preference", diet_opts, index=get_index(diet_opts, p_diet))
            
            if st.form_submit_button("Update Targets"):
                cals, prot, carbs, fats = calculate_macros(weight, height, bf, activity, goal, diet_type)
                conn = get_db_connection()
                # Update users table with new columns
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
        
        # Fetch latest body stats for defaults
        conn = get_db_connection()
        try:
            last_stat = conn.execute("SELECT weight_kg, bf_percent FROM body_stats ORDER BY date DESC LIMIT 1").fetchone()
        except:
            last_stat = None
        conn.close()
        
        # Use latest log if available, otherwise use profile weight
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
    
    # SAFEGUARD: Handle NoneType for user_goal if DB migration added null columns to existing row
    if user_goal is None:
        user_goal = "Maintain / Recomp"

    # --- DATE HANDLING & COMPENSATION ---
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
    
    # Adaptive Calorie Logic (Only adapt if strictly recomp/maintenance, skip for bulking/cutting to stay consistent)
    if "Maintain" in user_goal and y_cals > base_cals:
        overage = y_cals - base_cals
        deduction = min(overage, base_cals * 0.15)
        daily_target_cals -= deduction
        st.warning(f"📉 **Adaptive Adjustment:** Target reduced by {deduction:.0f} kcal due to yesterday's overage.")

    # --- TABS UI ---
    tab1, tab2 = st.tabs(["🍽️ Daily Tracker", "🤖 AI Coach"])

    # --- TAB 1: TRACKER ---
    with tab1:
        # Date Picker for Logging
        c_date, c_spacer = st.columns([1, 4])
        with c_date:
            view_date_obj = st.date_input("Tracking Date", value=datetime.now())
            view_date = view_date_obj.strftime("%Y-%m-%d")

        col1, col2 = st.columns([1.5, 1])
        
        if view_date == today:
            current_target_cals = daily_target_cals
            date_label = "Today's"
        else:
            current_target_cals = base_cals
            date_label = f"{view_date_obj.strftime('%b %d')}"

        with col1:
            st.subheader(f"{date_label} Overview")
            
            # Fetch Data for SELECTED Date
            conn = get_db_connection()
            stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(carbs), SUM(fats),
                                    SUM(fiber), SUM(sugar), SUM(sodium)
                                    FROM food_logs WHERE date = ?""", (view_date,)).fetchone()
            
            c_cal = stats[0] or 0
            c_prot = stats[1] or 0
            c_carb = stats[2] or 0
            c_fat = stats[3] or 0
            c_fiber = stats[4] or 0
            c_sugar = stats[5] or 0
            c_sodium = stats[6] or 0
            
            # --- DASHBOARD CARDS ---
            # 1. Main Goal Progress
            cal_progress = min(c_cal / current_target_cals, 1.0) if current_target_cals > 0 else 0
            st.markdown(f"**Calories:** {c_cal} / {current_target_cals:.0f} ({int(cal_progress*100)}%)")
            st.progress(cal_progress)
            
            # 2. Macro Bars with Custom Colors (using HTML for color control since st.progress is limited)
            def color_bar(label, val, target, color_hex, tooltip):
                pct = min(val/target, 1.0) * 100 if target > 0 else 0
                st.markdown(f"""
                <div style="margin-bottom: 5px;">
                    <span title="{tooltip}">{label}: <b>{val}g</b> / {target}g</span>
                    <div style="background-color: #e0e0e0; border-radius: 5px; height: 10px; width: 100%;">
                        <div style="background-color: {color_hex}; width: {pct}%; height: 100%; border-radius: 5px;"></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
            
            c_m1, c_m2, c_m3 = st.columns(3)
            with c_m1: color_bar("Protein", c_prot, t_prot, "#4CAF50", "Crucial for muscle repair and growth.")
            with c_m2: color_bar("Carbs", c_carb, t_carbs, "#2196F3", "Primary energy source.")
            with c_m3: color_bar("Fats", c_fat, t_fats, "#FFC107", "Essential for hormone regulation.")
            
            # 3. Micro Nutrient Dashboard
            st.markdown("---")
            st.caption("Nutrient Watchlist")
            m1, m2, m3 = st.columns(3)
            m1.metric("Fiber", f"{c_fiber}g", help="Target: ~30g for digestion")
            m2.metric("Sugar", f"{c_sugar}g", help="Monitor added sugars")
            m3.metric("Sodium", f"{c_sodium}mg", help="Target: <2300mg")

            st.divider()
            
            # Meal Logging Form - NOW PROPERLY INDENTED INSIDE COL1
            # --- MEAL LOGGING ---
            with st.container(border=True):
                st.markdown(f"#### ➕ Add Meal to {view_date}")
                f_name = st.text_input("Describe your meal", placeholder="e.g., Double cheeseburger no bun")
                f_note = st.text_input("Note (Optional)", placeholder="e.g., Ate out, Snack at work")
                
                if st.button("Log Meal", type="primary"):
                    if not f_name:
                        st.warning("Please describe your food first.")
                    else:
                        with st.spinner("Analyzing meal with Gemini..."):
                            data = analyze_food_with_gemini(f_name, f_note, active_api_key)
                            if data:
                                # --- SAFE NUTRIENT PARSING ---
                                calories = int(data.get('calories', 0) or 0)
                                
                                # Protein estimate: add safety check, min 1g if zero
                                protein = int(data.get('protein', 0) or 0)
                                if protein == 0 and calories > 0:
                                    # crude heuristic: 15-25% calories from protein if missing
                                    protein = max(1, round(calories * 0.2 / 4))
                                
                                carbs = int(data.get('carbs', 0) or 0)
                                fats = int(data.get('fats', 0) or 0)
                                fiber = int(data.get('fiber', 0) or 0)
                                sugar = int(data.get('sugar', 0) or 0)
                                sodium = int(data.get('sodium', 0) or 0)
                                
                                # --- DATABASE LOGGING ---
                                conn = get_db_connection()
                                conn.execute("""INSERT INTO food_logs 
                                    (date, food_name, amount_desc, calories, protein, carbs, fats, fiber, sugar, sodium, nutrients, note) 
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (view_date, 
                                     data.get('food_name', 'Unknown Food'), 
                                     f_name, 
                                     calories, protein, carbs, fats, 
                                     fiber, sugar, sodium, 
                                     data.get('micronutrients', ''), f_note))
                                conn.commit()
                                conn.close()
                                
                                st.success(f"Logged: {data.get('food_name', 'Unknown Food')}")
                                st.rerun()
                            else:
                                st.error("Could not analyze food. Try adjusting your description or check API key.")


        with col2:
            st.subheader("Logs")
            
            conn = get_db_connection()
            # Fetch logs including note
            logs = conn.execute("SELECT id, food_name, calories, protein, carbs, fats, note FROM food_logs WHERE date = ? ORDER BY id DESC", (view_date,)).fetchall()
            conn.close()
            
            if not logs:
                st.info(f"No meals logged for {view_date}.")
            else:
                for log in logs:
                    log_id, name, cal, prot, carb, fat, note = log
                    
                    with st.container(border=True):
                        c1, c2 = st.columns([4, 1])
                        with c1:
                            st.markdown(f"**{name}**")
                            if note:
                                st.caption(f"📝 *{note}*")
                            st.caption(f"🔥 {cal} | P:{prot} C:{carb} F:{fat}")
                        with c2:
                            if st.button("🗑️", key=f"del_{log_id}"):
                                conn = get_db_connection()
                                conn.execute("DELETE FROM food_logs WHERE id = ?", (log_id,))
                                conn.commit()
                                conn.close()
                                st.rerun()
                
                if st.button(f"Clear Day", type="secondary"):
                    conn = get_db_connection()
                    conn.execute("DELETE FROM food_logs WHERE date = ?", (view_date,))
                    conn.commit()
                    conn.close()
                    st.rerun()

    # --- TAB 2: AI COACH ---
    with tab2:
        st.subheader("🤖 AI Nutrition Coach")
        
        # --- PREPARE DATA FOR TAB 2 ---
        conn = get_db_connection()
        # Today's stats for context
        today_stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(fiber), SUM(sugar), SUM(sodium) 
                                      FROM food_logs WHERE date = ?""", (today,)).fetchone()
        
        # Historical Data for Consistency/Trends
        try:
            df_food = pd.read_sql_query("SELECT date, SUM(calories) as calories, SUM(protein) as protein, SUM(fiber) as fiber FROM food_logs GROUP BY date ORDER BY date", conn)
        except:
            df_food = pd.DataFrame()
        
        # Week Data for report
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_logs = pd.read_sql_query(f"SELECT * FROM food_logs WHERE date >= '{week_ago}'", conn)
        conn.close()

        # Parse Today's Current Status
        t_cur_cal = today_stats[0] or 0
        t_cur_prot = today_stats[1] or 0
        t_cur_fiber = today_stats[2] or 0
        t_cur_sugar = today_stats[3] or 0
        t_cur_sodium = today_stats[4] or 0
        
        rem_cals = daily_target_cals - t_cur_cal
        rem_prot = t_prot - t_cur_prot

        # --- SECTION 1: PLANNED MEAL ANALYZER (NEW) ---
        with st.container(border=True):
            st.markdown("### 🔮 Analyze a Planned Meal")
            st.info(f"**Remaining Today:** {rem_cals:.0f} Calories, {rem_prot}g Protein")
            
            c_input, c_btn = st.columns([3, 1])
            with c_input:
                planned_food = st.text_input("What are you planning to eat?", placeholder="e.g., A slice of pepperoni pizza and a coke")
            with c_btn:
                st.write("") # Spacer
                st.write("") 
                analyze_click = st.button("Analyze Plan", type="primary")
            
            if analyze_click:
                if not planned_food:
                    st.warning("Please enter a food item.")
                else:
                    with st.spinner("Consulting AI Coach..."):
                        cur_micros = {'fiber': t_cur_fiber, 'sugar': t_cur_sugar, 'sodium': t_cur_sodium}
                        advice = analyze_planned_meal(planned_food, int(rem_cals), int(rem_prot), cur_micros, active_api_key)
                        st.markdown(advice)

        st.divider()

        # --- SECTION 2: CONSISTENCY TRACKER (MOVED FROM TAB 3) ---
        st.markdown("### 🏆 Consistency Tracker")
        if not df_food.empty:
            df_food['hit_protein'] = df_food['protein'] >= (t_prot * 0.9) # Within 10%
            df_food['hit_cal'] = (df_food['calories'] >= (base_cals * 0.9)) & (df_food['calories'] <= (base_cals * 1.1))
            
            c1, c2, c3 = st.columns(3)
            
            prot_pct = (df_food['hit_protein'].sum() / len(df_food)) * 100
            cal_pct = (df_food['hit_cal'].sum() / len(df_food)) * 100
            
            # Simple streak calc
            # Sort by date desc
            df_sort = df_food.sort_values('date', ascending=False)
            streak = 0
            for hit in df_sort['hit_protein']:
                if hit: streak += 1
                else: break
            
            c1.metric("Protein Goal Hit Rate", f"{prot_pct:.1f}%")
            c2.metric("Calorie Goal Hit Rate", f"{cal_pct:.1f}%")
            c3.metric("Current Protein Streak", f"{streak} Days")
        else:
            st.info("Log meals to unlock consistency tracking.")

        st.divider()

        # --- SECTION 3: WEEKLY BREAKDOWNS (MOVED FROM TAB 3 & MERGED WITH REPORT) ---
        col_header, col_report_btn = st.columns([3, 1])
        with col_header:
            st.markdown("### 📅 Weekly Breakdowns")
        with col_report_btn:
             if not week_logs.empty:
                if st.button("Generate AI Report"):
                    with st.spinner("Analyzing weekly trends..."):
                        # Prepare summary string for AI
                        summary = week_logs.groupby('date')[['calories', 'protein', 'fiber', 'sugar']].sum().to_string()
                        report = get_weekly_analysis(summary, user_goal, active_api_key)
                        st.markdown(report)

        if not df_food.empty:
            df_food['date'] = pd.to_datetime(df_food['date'])
            df_food['week_start'] = df_food['date'].dt.to_period('W').apply(lambda r: r.start_time)
            
            weeks = df_food['week_start'].unique()
            
            for week_start in sorted(weeks, reverse=True):
                week_end = week_start + timedelta(days=6)
                w_data = df_food[df_food['week_start'] == week_start]
                
                avg_cal = w_data['calories'].mean()
                avg_prot = w_data['protein'].mean()
                avg_fiber = w_data['fiber'].mean()
                
                with st.expander(f"Week of {week_start.strftime('%b %d')}"):
                    wc1, wc2, wc3 = st.columns(3)
                    wc1.metric("Avg Calories", f"{avg_cal:.0f}", f"{avg_cal-base_cals:.0f}")
                    wc2.metric("Avg Protein", f"{avg_prot:.0f}", f"{avg_prot-t_prot:.0f}")
                    wc3.metric("Avg Fiber", f"{avg_fiber:.1f}g")
        else:
             st.info("No weekly data available yet.")

if __name__ == "__main__":
    main()
