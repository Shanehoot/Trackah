import streamlit as st
import google.generativeai as genai
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import json
import re

# --- CONFIGURATION & SETUP ---
st.set_page_config(page_title="AI Macro Tracker", layout="wide", page_icon="🧬")

# Try to get API key from secrets, otherwise ask user
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
except (FileNotFoundError, KeyError):
    # Fallback for local testing if secrets.toml isn't set up
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
    model = genai.GenerativeModel('gemini-2.0-flash')
    
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
        return extract_json(response.text)
    except Exception as e:
        st.error(f"AI Error: {e}")
        return None

def get_food_suggestion(rem_cals, rem_prot, rem_carbs, rem_fats, api_key):
    if not api_key: return "API Key missing."
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    prompt = f"""
    I have {rem_cals} calories left.
    Macros needed: Protein: {rem_prot}g, Carbs: {rem_carbs}g, Fats: {rem_fats}g.
    
    Suggest 3 COMPLETE meal options (not just ingredients).
    Include at least one option that can be bought at a convenience store (like 7-Eleven) or a common fast food chain.
    Format clearly.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Could not generate suggestions: {e}"

def get_weekly_analysis(week_data, goal, api_key):
    if not api_key: return "API Key missing."
    
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    
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

        st.divider()
        st.header("User Profile")
        with st.form("profile_form"):
            weight = st.number_input("Weight (kg)", value=70.0)
            height = st.number_input("Height (cm)", value=175.0)
            bf = st.number_input("Body Fat %", value=20.0)
            activity = st.selectbox("Activity Level", ["Sedentary", "Lightly Active", "Moderately Active", "Very Active"])
            
            st.subheader("Goals")
            goal = st.selectbox("Primary Goal", ["Maintain / Recomp", "Lose Weight", "Gain Muscle"])
            diet_type = st.selectbox("Diet Preference", ["Balanced", "High Protein", "Keto"])
            
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
        with st.expander("Record Weigh-in"):
            with st.form("weight_log"):
                log_date = st.date_input("Date", value=datetime.now())
                log_weight = st.number_input("Current Weight (kg)", value=weight)
                log_bf = st.number_input("Current BF % (optional)", value=bf)
                
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
    user_data = conn.execute("SELECT target_calories, target_protein, target_carbs, target_fats, goal FROM users WHERE id=1").fetchone()
    conn.close()

    if not user_data:
        st.info("👈 Please set your profile in the sidebar to begin.")
        return
        
    base_cals, t_prot, t_carbs, t_fats, user_goal = user_data

    # --- DATE HANDLING & COMPENSATION ---
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    conn = get_db_connection()
    y_stats = conn.execute("SELECT SUM(calories) FROM food_logs WHERE date = ?", (yesterday,)).fetchone()
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
    tab1, tab2, tab3 = st.tabs(["🍽️ Daily Tracker", "🤖 AI Coach", "📈 Comprehensive Trends"])

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
            
            # Meal Logging Form
            with st.container(border=True):
                st.markdown(f"#### ➕ Add Meal to {view_date}")
                f_name = st.text_input("Describe your meal", placeholder="e.g., Double cheeseburger no bun")
                f_note = st.text_input("Note (Optional)", placeholder="e.g., Ate out, Snack at work")
                
                if st.button("Log Meal", type="primary"):
                    if not f_name:
                        st.warning("Please describe your food first.")
                    else:
                        with st.spinner("Analyzing..."):
                            data = analyze_food_with_gemini(f_name, f_note, active_api_key)
                            if data:
                                conn = get_db_connection()
                                conn.execute("""INSERT INTO food_logs 
                                    (date, food_name, amount_desc, calories, protein, carbs, fats, fiber, sugar, sodium, nutrients, note) 
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (view_date, data['food_name'], f_name, 
                                     data['calories'], data['protein'], data['carbs'], data['fats'],
                                     data.get('fiber', 0), data.get('sugar', 0), data.get('sodium', 0),
                                     data.get('micronutrients', ''), f_note))
                                conn.commit()
                                conn.close()
                                st.success(f"Logged: {data['food_name']}")
                                st.rerun()

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
        st.subheader("🤖 AI Nutritionist")
        
        # Calculate stats specifically for TODAY
        conn = get_db_connection()
        today_stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(carbs), SUM(fats) 
                                FROM food_logs WHERE date = ?""", (today,)).fetchone()
        
        # Get last 7 days for weekly analysis
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_logs = pd.read_sql_query(f"SELECT * FROM food_logs WHERE date >= '{week_ago}'", conn)
        conn.close()

        t_cur_cal = today_stats[0] or 0
        t_cur_prot = today_stats[1] or 0
        t_cur_carb = today_stats[2] or 0
        t_cur_fat = today_stats[3] or 0

        rem_cals = daily_target_cals - t_cur_cal
        rem_prot = t_prot - t_cur_prot
        rem_carbs = t_carbs - t_cur_carb
        rem_fats = t_fats - t_cur_fat
        
        col_sugg, col_analysis = st.columns(2)
        
        with col_sugg:
            with st.container(border=True):
                st.markdown("#### 💡 Next Meal Suggestions")
                if rem_cals <= 0:
                    st.success("Target hit! No more food needed unless you're truly hungry.")
                else:
                    st.info(f"Gap: {rem_cals:.0f} cal, {rem_prot}g Prot")
                    if st.button("Get Meal Ideas"):
                        with st.spinner("Finding convenient options..."):
                            suggestion = get_food_suggestion(int(rem_cals), int(rem_prot), int(rem_carbs), int(rem_fats), active_api_key)
                            st.markdown(suggestion)

        with col_analysis:
            with st.container(border=True):
                st.markdown("#### 📅 Weekly Check-in")
                if week_logs.empty:
                    st.warning("Not enough data for weekly analysis.")
                else:
                    if st.button("Generate Weekly Report"):
                        with st.spinner("Analyzing trends..."):
                            # Prepare summary string for AI
                            summary = week_logs.groupby('date')[['calories', 'protein', 'fiber', 'sugar']].sum().to_string()
                            report = get_weekly_analysis(summary, user_goal, active_api_key)
                            st.markdown(report)

    # --- TAB 3: OVERALL TRENDS ---
    with tab3:
        st.subheader("📊 Comprehensive Progress")
        
        conn = get_db_connection()
        
        # 1. Nutrition Data
        df_food = pd.read_sql_query("SELECT date, SUM(calories) as calories, SUM(protein) as protein, SUM(fiber) as fiber FROM food_logs GROUP BY date ORDER BY date", conn)
        
        # 2. Body Data
        df_body = pd.read_sql_query("SELECT date, weight_kg, bf_percent FROM body_stats ORDER BY date", conn)
        conn.close()
        
        if df_food.empty and df_body.empty:
            st.info("Start logging to see trends.")
        else:
            # --- SECTION A: WEIGHT vs CALORIES ---
            st.markdown("### Weight vs Calorie Intake")
            if not df_body.empty:
                # Merge dataframes on date
                df_merged = pd.merge(df_food, df_body, on='date', how='outer').fillna(0)
                df_merged['date'] = pd.to_datetime(df_merged['date'])
                df_merged = df_merged.sort_values('date')
                
                # Plot
                st.line_chart(df_merged.set_index('date')[['weight_kg', 'calories']])
            else:
                st.warning("Log your weight in the sidebar to see correlations.")

            # --- SECTION B: CONSISTENCY & STREAKS ---
            st.divider()
            st.markdown("### Consistency Tracker")
            
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

            # --- SECTION C: WEEKLY BREAKDOWNS ---
            st.divider()
            st.markdown("### Weekly Breakdowns")
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
                    
                    with st.expander(f"Week: {week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}"):
                        wc1, wc2, wc3 = st.columns(3)
                        wc1.metric("Avg Calories", f"{avg_cal:.0f}", f"{avg_cal-base_cals:.0f}")
                        wc2.metric("Avg Protein", f"{avg_prot:.0f}", f"{avg_prot-t_prot:.0f}")
                        wc3.metric("Avg Fiber", f"{avg_fiber:.1f}g")

if __name__ == "__main__":
    main()
