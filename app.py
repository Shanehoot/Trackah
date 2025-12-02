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
    
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (id INTEGER PRIMARY KEY, height_cm REAL, weight_kg REAL, 
                  bf_percent REAL, activity_level TEXT, 
                  target_calories REAL, target_protein REAL, 
                  target_carbs REAL, target_fats REAL)''')
    
    # Food logs table
    c.execute('''CREATE TABLE IF NOT EXISTS food_logs 
                 (id INTEGER PRIMARY KEY, date TEXT, food_name TEXT, 
                  amount_desc TEXT, calories INTEGER, 
                  protein INTEGER, carbs INTEGER, fats INTEGER, 
                  nutrients TEXT)''')
    
    conn.commit()
    conn.close()

# --- UTILITIES ---
def extract_json(text):
    """
    Robustly extracts JSON from AI response, handling markdown fences 
    and extra conversational text.
    """
    try:
        # First attempt: standard clean
        clean_text = text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text.replace("```json", "").replace("```", "")
        
        return json.loads(clean_text)
    except json.JSONDecodeError:
        # Second attempt: Regex extraction if there is extra text
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except:
                pass
        return None

# --- CALCULATIONS ---
def calculate_macros(weight, height, bf_percent, activity_level):
    # Katch-McArdle Formula
    lean_mass_kg = weight * (1 - (bf_percent / 100))
    bmr = 370 + (21.6 * lean_mass_kg)
    
    activity_multipliers = {
        "Sedentary": 1.2, "Lightly Active": 1.375,
        "Moderately Active": 1.55, "Very Active": 1.725
    }
    tdee = bmr * activity_multipliers.get(activity_level, 1.2)
    
    # Recomp Strategy (Maintenance Calories)
    target_calories = round(tdee) 
    
    # Macro Split
    # Protein: 2.2g/kg of lean mass
    target_protein = round(lean_mass_kg * 2.2)
    # Fats: 0.8g/kg of body weight
    target_fats = round(weight * 0.8)
    
    # Carbs: Remainder
    cals_from_prot_fat = (target_protein * 4) + (target_fats * 9)
    remaining_cals = target_calories - cals_from_prot_fat
    target_carbs = round(max(0, remaining_cals / 4))
    
    return target_calories, target_protein, target_carbs, target_fats

# --- AI INTEGRATION ---
def analyze_food_with_gemini(food_input, api_key):
    if not api_key or "YOUR_API_KEY" in api_key:
        st.error("Please provide a valid API Key.")
        return None

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash') # Using faster/cheaper model
    
    prompt = f"""
    Analyze this food input: "{food_input}".
    Estimate values based on standard data.
    
    Return ONLY a raw JSON string with this structure:
    {{
        "food_name": "Short concise name",
        "calories": int,
        "protein": int,
        "carbs": int,
        "fats": int,
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
    Suggest 3 specific snack/meal options. Keep it brief.
    """
    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Could not generate suggestions: {e}"

# --- MAIN APP ---
def main():
    init_db()
    
    st.title("🧬 AI Body Recomposition Tracker")

    # --- SIDEBAR: CONFIG ---
    with st.sidebar:
        st.header("Settings")
        
        # API Key Input (if not in secrets)
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
            
            if st.form_submit_button("Update Targets"):
                cals, prot, carbs, fats = calculate_macros(weight, height, bf, activity)
                conn = get_db_connection()
                conn.execute("""INSERT OR REPLACE INTO users 
                                (id, height_cm, weight_kg, bf_percent, activity_level, 
                                 target_calories, target_protein, target_carbs, target_fats) 
                                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)""", 
                             (height, weight, bf, activity, cals, prot, carbs, fats))
                conn.commit()
                conn.close()
                st.toast("Macros Recalculated!")
                st.rerun()

    # --- LOAD USER DATA ---
    conn = get_db_connection()
    user = conn.execute("SELECT target_calories, target_protein, target_carbs, target_fats FROM users WHERE id=1").fetchone()
    conn.close()
    
    if not user:
        st.info("👈 Please set your profile in the sidebar to begin.")
        return
        
    base_cals, t_prot, t_carbs, t_fats = user

    # --- DATE HANDLING & COMPENSATION ---
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    conn = get_db_connection()
    y_stats = conn.execute("SELECT SUM(calories) FROM food_logs WHERE date = ?", (yesterday,)).fetchone()
    conn.close()
    
    y_cals = y_stats[0] if y_stats and y_stats[0] else 0
    daily_target_cals = base_cals
    
    # Adaptive Calorie Logic
    if y_cals > base_cals:
        overage = y_cals - base_cals
        deduction = min(overage, base_cals * 0.15) # Cap deduction at 15% to prevent starvation
        daily_target_cals -= deduction
        st.warning(f"📉 **Adaptive Adjustment:** Target reduced by {deduction:.0f} kcal due to yesterday's overage.")

    # --- TABS UI ---
    tab1, tab2, tab3 = st.tabs(["🍽️ Daily Tracker", "📈 Trends", "🤖 AI Coach"])

    # --- TAB 1: TRACKER ---
    with tab1:
        col1, col2 = st.columns([1.5, 1])
        
        with col1:
            st.subheader("Today's Macros")
            
            # Fetch Today's Data
            conn = get_db_connection()
            stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(carbs), SUM(fats) 
                                    FROM food_logs WHERE date = ?""", (today,)).fetchone()
            
            c_cal = stats[0] or 0
            c_prot = stats[1] or 0
            c_carb = stats[2] or 0
            c_fat = stats[3] or 0
            
            # Helper for metrics
            def macro_metric(label, current, target, unit="g"):
                delta = target - current
                color = "normal" if delta > 0 else "off"
                st.metric(label, f"{current}{unit}", f"{delta}{unit} left", delta_color=color)

            # Visual Progress
            st.progress(min(c_cal / daily_target_cals, 1.0) if daily_target_cals > 0 else 0)
            st.caption(f"Calories: {c_cal} / {daily_target_cals:.0f} kcal")

            m1, m2, m3 = st.columns(3)
            with m1: macro_metric("Protein", c_prot, t_prot)
            with m2: macro_metric("Carbs", c_carb, t_carbs)
            with m3: macro_metric("Fats", c_fat, t_fats)

            st.divider()
            
            # Meal Logging Form
            with st.container(border=True):
                st.markdown("#### ➕ Add Meal")
                food_input = st.text_input("Describe your meal", placeholder="e.g., Chicken breast 200g and rice")
                
                if st.button("Log Meal", type="primary"):
                    if not food_input:
                        st.warning("Please describe your food first.")
                    else:
                        with st.spinner("Analyzing..."):
                            data = analyze_food_with_gemini(food_input, active_api_key)
                            if data:
                                conn = get_db_connection()
                                conn.execute("""INSERT INTO food_logs 
                                    (date, food_name, amount_desc, calories, protein, carbs, fats, nutrients) 
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (today, data['food_name'], food_input, 
                                     data['calories'], data['protein'], data['carbs'], data['fats'], 
                                     data['micronutrients']))
                                conn.commit()
                                conn.close()
                                st.success(f"Logged: {data['food_name']}")
                                st.rerun()

        with col2:
            st.subheader("Recent Logs")
            df = pd.read_sql_query(f"SELECT food_name, calories, protein, carbs, fats FROM food_logs WHERE date = '{today}' ORDER BY id DESC", get_db_connection())
            
            if df.empty:
                st.info("No meals logged today yet.")
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)
                
                if st.button("Clear Today's Log"):
                    conn = get_db_connection()
                    conn.execute("DELETE FROM food_logs WHERE date = ?", (today,))
                    conn.commit()
                    conn.close()
                    st.rerun()

    # --- TAB 2: TRENDS ---
    with tab2:
        st.subheader("Weekly Adherence")
        conn = get_db_connection()
        
        # Get last 7 days data
        query = """
            SELECT date, SUM(calories) as calories, SUM(protein) as protein 
            FROM food_logs 
            GROUP BY date 
            ORDER BY date DESC LIMIT 7
        """
        df_trend = pd.read_sql_query(query, conn)
        conn.close()
        
        if not df_trend.empty:
            st.bar_chart(df_trend.set_index('date'))
        else:
            st.info("Not enough data for trends yet.")

    # --- TAB 3: COACH ---
    with tab3:
        st.subheader("🤖 AI Nutritionist")
        
        rem_cals = daily_target_cals - c_cal
        rem_prot = t_prot - c_prot
        rem_carbs = t_carbs - c_carb
        rem_fats = t_fats - c_fat
        
        if rem_cals <= 0:
            st.success("You've hit your calorie target for the day! Great job.")
        else:
            st.markdown(f"""
            **Current Gap:**
            - **{rem_cals:.0f}** Calories
            - **{rem_prot}g** Protein
            - **{rem_carbs}g** Carbs
            - **{rem_fats}g** Fats
            """)
            
            if st.button("Suggest Meal to Fill Gap"):
                with st.spinner("Thinking..."):
                    suggestion = get_food_suggestion(int(rem_cals), int(rem_prot), int(rem_carbs), int(rem_fats), active_api_key)
                    st.markdown(suggestion)

if __name__ == "__main__":
    main()
