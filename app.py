import streamlit as st

import google.generativeai as genai

import sqlite3

import pandas as pd

from datetime import datetime, timedelta

import json



# --- CONFIGURATION ---

# Replace with your actual API Key

API_KEY = "YOUR_GEMINI_API_KEY_HERE" 



try:

    genai.configure(api_key=API_KEY)

except Exception as e:

    st.error(f"API Key Error: {e}")



# --- DATABASE SETUP ---

def init_db():

    conn = sqlite3.connect('fitness_data.db')

    c = conn.cursor()

    # Updated User Table with macro targets

    c.execute('''CREATE TABLE IF NOT EXISTS users 

                 (id INTEGER PRIMARY KEY, height_cm REAL, weight_kg REAL, 

                  bf_percent REAL, activity_level TEXT, 

                  target_calories REAL, target_protein REAL, 

                  target_carbs REAL, target_fats REAL)''')

    

    # Updated Food Log with Carbs/Fats

    c.execute('''CREATE TABLE IF NOT EXISTS food_logs 

                 (id INTEGER PRIMARY KEY, date TEXT, food_name TEXT, 

                  amount_desc TEXT, calories INTEGER, 

                  protein INTEGER, carbs INTEGER, fats INTEGER, 

                  nutrients TEXT)''')

                  

    c.execute('''CREATE TABLE IF NOT EXISTS workouts 

                 (id INTEGER PRIMARY KEY, date TEXT, description TEXT, duration_mins INTEGER)''')

    conn.commit()

    conn.close()



def get_db_connection():

    return sqlite3.connect('fitness_data.db')



# --- CALCULATIONS (BODY RECOMP + SPLIT) ---

def calculate_macros(weight, height, bf_percent, activity_level):

    # 1. Calculate BMR & TDEE (Katch-McArdle)

    lean_mass_kg = weight * (1 - (bf_percent / 100))

    bmr = 370 + (21.6 * lean_mass_kg)

    

    activity_multipliers = {

        "Sedentary": 1.2, "Lightly Active": 1.375,

        "Moderately Active": 1.55, "Very Active": 1.725

    }

    tdee = bmr * activity_multipliers.get(activity_level, 1.2)

    

    # 2. Set Targets (Recomp Strategy)

    target_calories = round(tdee) # Maintenance

    

    # 3. Macro Split

    # Protein: 2.2g/kg of lean mass (High for muscle retention)

    target_protein = round(lean_mass_kg * 2.2)

    

    # Fats: 0.8g/kg of body weight (Hormonal health)

    target_fats = round(weight * 0.8)

    

    # Carbs: Fill the remaining calories

    # 1g Prot = 4cal, 1g Fat = 9cal, 1g Carb = 4cal

    cals_from_prot_fat = (target_protein * 4) + (target_fats * 9)

    remaining_cals = target_calories - cals_from_prot_fat

    target_carbs = round(max(0, remaining_cals / 4))

    

    return target_calories, target_protein, target_carbs, target_fats



# --- AI INTEGRATION (UPDATED PROMPT) ---

def analyze_food_with_gemini(food_input):

    model = genai.GenerativeModel('gemini-pro')

    # Refined prompt for specific macro splitting

    prompt = f"""

    You are an expert nutritionist. Analyze this food input: "{food_input}".

    Estimate the following values based on standard nutritional data.

    

    Return ONLY a raw JSON string (no markdown, no backticks) with this exact structure:

    {{

        "food_name": "Standardized Food Name",

        "calories": int,

        "protein": int,

        "carbs": int,

        "fats": int,

        "micronutrients": "List 3 key vitamins/minerals separated by commas"

    }}

    """

    try:

        response = model.generate_content(prompt)

        clean_text = response.text.replace('```json', '').replace('```', '').strip()

        return json.loads(clean_text)

    except Exception as e:

        st.error(f"AI Error: {e}")

        return None



def get_food_suggestion(rem_cals, rem_prot, rem_carbs, rem_fats):

    model = genai.GenerativeModel('gemini-pro')

    prompt = f"""

    I have {rem_cals} calories left today.

    My remaining macro requirements are:

    - Protein: {rem_prot}g

    - Carbs: {rem_carbs}g

    - Fats: {rem_fats}g

    

    Suggest 3 specific snack/meal options that fit reasonably well into these remaining stats. 

    Explain why they fit (e.g., "High protein, low fat").

    """

    response = model.generate_content(prompt)

    return response.text



# --- UI & LOGIC ---

def main():

    st.set_page_config(page_title="AI Macro Tracker", layout="wide")

    init_db()

    

    st.title("🧬 AI Body Recomposition & Macro Tracker")



    # --- SIDEBAR ---

    st.sidebar.header("User Profile")

    with st.sidebar.form("profile_form"):

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



    # Load targets

    conn = get_db_connection()

    user = conn.execute("SELECT target_calories, target_protein, target_carbs, target_fats FROM users WHERE id=1").fetchone()

    conn.close()

    

    if not user:

        st.info("👈 Please set your profile in the sidebar.")

        return

        

    base_cals, t_prot, t_carbs, t_fats = user



    # --- COMPENSATION LOGIC ---

    today = datetime.now().strftime("%Y-%m-%d")

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    

    conn = get_db_connection()

    y_stats = conn.execute("SELECT SUM(calories) FROM food_logs WHERE date = ?", (yesterday,)).fetchone()

    conn.close()

    

    y_cals = y_stats[0] if y_stats and y_stats[0] else 0

    daily_target_cals = base_cals

    comp_msg = ""

    

    if y_cals > base_cals:

        overage = y_cals - base_cals

        deduction = min(overage, base_cals * 0.15)

        daily_target_cals -= deduction

        comp_msg = f"📉 Goal adjusted: -{deduction:.0f} kcal (due to yesterday's overage)."



    # --- TABS ---

    tab1, tab2, tab3 = st.tabs(["🍽️ Tracker", "📈 Weekly Trends", "🤖 AI Coach"])



    # --- TAB 1: TRACKER ---

    with tab1:

        col1, col2 = st.columns([1, 1])

        

        with col1:

            st.subheader("Log Meal")

            food_input = st.text_input("Describe your meal", placeholder="e.g. 200g steak and a medium potato")

            if st.button("Analyze & Log"):

                if "API_KEY" in API_KEY: st.error("Add API Key in code")

                else:

                    with st.spinner("Analyzing macros..."):

                        data = analyze_food_with_gemini(food_input)

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



        with col2:

            st.subheader("Daily Progress")

            conn = get_db_connection()

            stats = conn.execute("""SELECT SUM(calories), SUM(protein), SUM(carbs), SUM(fats) 

                                    FROM food_logs WHERE date = ?""", (today,)).fetchone()

            conn.close()

            

            # Safe defaults if None

            c_cal = stats[0] or 0

            c_prot = stats[1] or 0

            c_carb = stats[2] or 0

            c_fat = stats[3] or 0

            

            if comp_msg: st.warning(comp_msg)

            

            # Progress Bars

            st.caption(f"Calories: {c_cal} / {daily_target_cals:.0f}")

            st.progress(min(c_cal / daily_target_cals, 1.0) if daily_target_cals > 0 else 0)

            

            c1, c2, c3 = st.columns(3)

            c1.metric("Protein", f"{c_prot}g", f"/{t_prot}g")

            c2.metric("Carbs", f"{c_carb}g", f"/{t_carbs}g")

            c3.metric("Fats", f"{c_fat}g", f"/{t_fats}g")



        # Today's History

        st.divider()

        st.write("### 📝 Today's Logs")

        conn = get_db_connection()

        df = pd.read_sql_query(f"SELECT food_name, calories, protein, carbs, fats FROM food_logs WHERE date = '{today}'", conn)

        conn.close()

        st.dataframe(df, use_container_width=True)



    # --- TAB 2: WEEKLY ---

    with tab2:

        st.subheader("Weekly Folder")

        conn = get_db_connection()

        df_weekly = pd.read_sql_query("""

            SELECT strftime('%W', date) as week_num, 

                   COUNT(DISTINCT date) as days_active,

                   SUM(calories) as tot_cal, 

                   SUM(protein) as tot_prot,

                   SUM(carbs) as tot_carb,

                   SUM(fats) as tot_fat

            FROM food_logs 

            GROUP BY week_num ORDER BY week_num DESC

        """, conn)

        conn.close()

        

        for idx, row in df_weekly.iterrows():

            with st.expander(f"Week {row['week_num']} (Days Active: {row['days_active']})"):

                # Rough comparison (multiplying daily target by days active to be fair)

                days = row['days_active']

                st.write(f"**Total Calories:** {row['tot_cal']} (Target ~{base_cals*days})")

                

                # Visual comparison

                cols = st.columns(3)

                cols[0].metric("Avg Protein/Day", f"{row['tot_prot']/days:.1f}g")

                cols[1].metric("Avg Carbs/Day", f"{row['tot_carb']/days:.1f}g")

                cols[2].metric("Avg Fats/Day", f"{row['tot_fat']/days:.1f}g")



    # --- TAB 3: COACH ---

    with tab3:

        st.subheader("Smart Suggestions")

        rem_cals = daily_target_cals - c_cal

        rem_prot = t_prot - c_prot

        rem_carbs = t_carbs - c_carb

        rem_fats = t_fats - c_fat

        

        st.info(f"Remaining: {rem_cals:.0f} kcal")

        

        if st.button("Suggest Meals for Remaining Macros"):

            with st.spinner("Chef Gemini is thinking..."):

                suggestion = get_food_suggestion(int(rem_cals), int(rem_prot), int(rem_carbs), int(rem_fats))

                st.markdown(suggestion)



if __name__ == "__main__":

    main()
