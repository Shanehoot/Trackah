import streamlit as st
import google.generativeai as genai
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import json
import re
import os
import uuid

# --- DEBUGGING GLOBALS ---
IMPORT_ERROR = None
HAS_FIRESTORE_LIB = False

# --- FIRESTORE SETUP ---
try:
    from google.cloud import firestore
    from google.oauth2 import service_account
    HAS_FIRESTORE_LIB = True
except ImportError as e:
    HAS_FIRESTORE_LIB = False
    IMPORT_ERROR = str(e)

# --- CONFIGURATION & SETUP ---
st.set_page_config(page_title="AI Macro Tracker", layout="wide", page_icon="🧬")

# Try to get API key from secrets, otherwise ask user
try:
    API_KEY = st.secrets["GEMINI_API_KEY"]
except (FileNotFoundError, KeyError):
    API_KEY = "YOUR_API_KEY_HERE" 

# --- DATA MANAGER CLASS (HYBRID: SQLITE OR FIRESTORE) ---
class DataManager:
    def __init__(self):
        self.use_firestore = False
        self.db = None
        self.sqlite_db = 'fitness_data.db'
        self.connection_error = None
        
        # Check for Firestore secrets and library
        if HAS_FIRESTORE_LIB and "gcp_service_account" in st.secrets:
            try:
                # Parse the secrets dictionary
                key_dict = dict(st.secrets["gcp_service_account"])
                
                # Fix common newline issues in private_key if copy-pasted incorrectly
                if "private_key" in key_dict:
                    key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")

                creds = service_account.Credentials.from_service_account_info(key_dict)
                
                # Use the custom 'tracker' database
                self.db = firestore.Client(
                    credentials=creds, 
                    project=key_dict['project_id'], 
                    database='tracker' 
                )
                self.use_firestore = True
            except Exception as e:
                self.connection_error = str(e)
        
        # Fallback to SQLite if Firestore is not available
        if not self.use_firestore:
            self._init_sqlite()

    def _init_sqlite(self):
        conn = sqlite3.connect(self.sqlite_db)
        c = conn.cursor()
        
        # Users table
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                     (id INTEGER PRIMARY KEY, height_cm REAL, weight_kg REAL, 
                      bf_percent REAL, activity_level TEXT, 
                      goal TEXT, diet_type TEXT,
                      target_calories REAL, target_protein REAL, 
                      target_carbs REAL, target_fats REAL)''')
        
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
                      nutrients TEXT, note TEXT, uid TEXT)''')
        
        # Body Stats
        c.execute('''CREATE TABLE IF NOT EXISTS body_stats 
                     (id INTEGER PRIMARY KEY, date TEXT, weight_kg REAL, bf_percent REAL)''')
        
        # Templates Table (NEW)
        c.execute('''CREATE TABLE IF NOT EXISTS templates
                     (id INTEGER PRIMARY KEY, name TEXT, food_items_json TEXT, 
                      total_calories INTEGER, total_protein INTEGER, 
                      default_type TEXT, uid TEXT)''')

        conn.commit()
        conn.close()

    # --- USER PROFILE METHODS ---
    def get_user_profile(self):
        if self.use_firestore:
            doc = self.db.collection('users').document('profile').get()
            if doc.exists:
                return doc.to_dict()
            return None
        else:
            conn = sqlite3.connect(self.sqlite_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
            conn.close()
            return dict(row) if row else None

    def update_user_profile(self, data):
        if self.use_firestore:
            self.db.collection('users').document('profile').set(data)
        else:
            conn = sqlite3.connect(self.sqlite_db)
            exists = conn.execute("SELECT 1 FROM users WHERE id=1").fetchone()
            if exists:
                conn.execute("""UPDATE users SET height_cm=?, weight_kg=?, bf_percent=?, activity_level=?, goal=?, diet_type=?,
                                target_calories=?, target_protein=?, target_carbs=?, target_fats=? WHERE id=1""",
                             (data['height_cm'], data['weight_kg'], data['bf_percent'], data['activity_level'], 
                              data['goal'], data['diet_type'], data['target_calories'], data['target_protein'], 
                              data['target_carbs'], data['target_fats']))
            else:
                conn.execute("""INSERT INTO users (id, height_cm, weight_kg, bf_percent, activity_level, goal, diet_type,
                                target_calories, target_protein, target_carbs, target_fats)
                                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (data['height_cm'], data['weight_kg'], data['bf_percent'], data['activity_level'], 
                              data['goal'], data['diet_type'], data['target_calories'], data['target_protein'], 
                              data['target_carbs'], data['target_fats']))
            conn.commit()
            conn.close()

    # --- LOGGING METHODS ---
    def add_food_log(self, data):
        unique_id = str(uuid.uuid4())
        data['uid'] = unique_id
        
        if self.use_firestore:
            self.db.collection('food_logs').add(data)
        else:
            conn = sqlite3.connect(self.sqlite_db)
            conn.execute("""INSERT INTO food_logs 
                (date, food_name, amount_desc, calories, protein, carbs, fats, fiber, sugar, sodium, saturated_fat,
                 vitamin_a, vitamin_c, vitamin_d, calcium, iron, potassium, magnesium, zinc, note, uid) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (data['date'], data['food_name'], data['amount_desc'], data['calories'], data['protein'], 
                 data['carbs'], data['fats'], data['fiber'], data['sugar'], data['sodium'], data['saturated_fat'],
                 data['vitamin_a'], data['vitamin_c'], data['vitamin_d'], data['calcium'], data['iron'], 
                 data['potassium'], data['magnesium'], data['zinc'], data['note'], unique_id))
            conn.commit()
            conn.close()

    def delete_food_log(self, log_id):
        if self.use_firestore:
            self.db.collection('food_logs').document(log_id).delete()
        else:
            conn = sqlite3.connect(self.sqlite_db)
            try:
                conn.execute("DELETE FROM food_logs WHERE id=?", (int(log_id),))
            except:
                conn.execute("DELETE FROM food_logs WHERE uid=?", (log_id,))
            conn.commit()
            conn.close()
            
    def delete_day_logs(self, date_str):
        if self.use_firestore:
            docs = self.db.collection('food_logs').where('date', '==', date_str).stream()
            for doc in docs:
                doc.reference.delete()
        else:
            conn = sqlite3.connect(self.sqlite_db)
            conn.execute("DELETE FROM food_logs WHERE date=?", (date_str,))
            conn.commit()
            conn.close()

    def get_logs_for_date(self, date_str):
        if self.use_firestore:
            docs = self.db.collection('food_logs').where('date', '==', date_str).stream()
            logs = []
            for doc in docs:
                d = doc.to_dict()
                d['id'] = doc.id 
                defaults = {'calories':0, 'protein':0, 'carbs':0, 'fats':0, 'fiber':0, 'sugar':0, 'sodium':0, 'saturated_fat':0}
                for k,v in defaults.items():
                    if k not in d: d[k] = v
                logs.append(d)
            return logs
        else:
            conn = sqlite3.connect(self.sqlite_db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM food_logs WHERE date=?", (date_str,)).fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def get_logs_history(self, start_date_str):
        if self.use_firestore:
            docs = self.db.collection('food_logs').where('date', '>=', start_date_str).stream()
            return [doc.to_dict() for doc in docs]
        else:
            conn = sqlite3.connect(self.sqlite_db)
            df = pd.read_sql_query(f"SELECT * FROM food_logs WHERE date >= '{start_date_str}'", conn)
            conn.close()
            return df.to_dict('records')

    # --- BODY STATS ---
    def add_body_stat(self, data):
        if self.use_firestore:
            self.db.collection('body_stats').add(data)
        else:
            conn = sqlite3.connect(self.sqlite_db)
            conn.execute("INSERT INTO body_stats (date, weight_kg, bf_percent) VALUES (?, ?, ?)",
                         (data['date'], data['weight_kg'], data['bf_percent']))
            conn.commit()
            conn.close()
            
    def get_body_stats_history(self):
        if self.use_firestore:
            docs = self.db.collection('body_stats').stream()
            return [doc.to_dict() for doc in docs]
        else:
            conn = sqlite3.connect(self.sqlite_db)
            df = pd.read_sql_query("SELECT * FROM body_stats ORDER BY date", conn)
            conn.close()
            return df.to_dict('records')

    def get_latest_body_stat(self):
        stats = self.get_body_stats_history()
        if stats:
            # sort by date
            stats.sort(key=lambda x: x['date'], reverse=True)
            return stats[0]
        return None

    # --- TEMPLATES METHODS ---
    def add_template(self, name, food_data, default_type="None"):
        # Store the entire data object as JSON string for SQLite compatibility
        # In Firestore we could store as map, but string is safer for hybrid compatibility
        data_str = json.dumps(food_data)
        unique_id = str(uuid.uuid4())
        
        if self.use_firestore:
            self.db.collection('templates').add({
                'name': name,
                'food_items_json': data_str,
                'total_calories': food_data.get('calories', 0),
                'total_protein': food_data.get('protein', 0),
                'default_type': default_type,
                'uid': unique_id
            })
        else:
            conn = sqlite3.connect(self.sqlite_db)
            conn.execute("INSERT INTO templates (name, food_items_json, total_calories, total_protein, default_type, uid) VALUES (?, ?, ?, ?, ?, ?)",
                         (name, data_str, food_data.get('calories', 0), food_data.get('protein', 0), default_type, unique_id))
            conn.commit()
            conn.close()

    def get_templates(self):
        if self.use_firestore:
            docs = self.db.collection('templates').stream()
            templates = []
            for doc in docs:
                d = doc.to_dict()
                d['id'] = doc.id
                templates.append(d)
            return templates
        else:
            conn = sqlite3.connect(self.sqlite_db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM templates").fetchall()
            conn.close()
            return [dict(r) for r in rows]

    def delete_template(self, t_id):
        if self.use_firestore:
             self.db.collection('templates').document(t_id).delete()
        else:
            conn = sqlite3.connect(self.sqlite_db)
            # Try int id then uid
            try: conn.execute("DELETE FROM templates WHERE id=?", (int(t_id),))
            except: conn.execute("DELETE FROM templates WHERE uid=?", (t_id,))
            conn.commit()
            conn.close()

# Initialize Data Manager
dm = DataManager()

# --- UTILITIES ---
def extract_json(text):
    try:
        clean_text = text.strip()
        # Find the first { and last }
        start = clean_text.find('{')
        end = clean_text.rfind('}') + 1
        if start != -1 and end != -1:
            json_str = clean_text[start:end]
            return json.loads(json_str)
        return None
    except Exception:
        return None

# --- CALCULATIONS & AUTO ADJUST ---
def calculate_macros(weight, height, bf_percent, activity_level, goal, diet_type):
    lean_mass_kg = weight * (1 - (bf_percent / 100))
    bmr = 370 + (21.6 * lean_mass_kg)
    activity_multipliers = {"Sedentary": 1.2, "Lightly Active": 1.375, "Moderately Active": 1.55, "Very Active": 1.725}
    tdee = bmr * activity_multipliers.get(activity_level, 1.2)
    
    if goal == "Lose Weight": target_calories = round(tdee - 500)
    elif goal == "Gain Muscle": target_calories = round(tdee + 300)
    else: target_calories = round(tdee)
    
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

def check_macro_auto_adjust(weight_history, current_target_cals):
    """
    Checks weight loss rate over last 14 days.
    Returns: (bool: recommended_change, str: message, int: new_calories)
    """
    if len(weight_history) < 2:
        return False, "Not enough weight data", current_target_cals
        
    df = pd.DataFrame(weight_history)
    df['date'] = pd.to_datetime(df['date'])
    df = df.sort_values('date')
    
    # Filter last 14 days
    two_weeks_ago = datetime.now() - timedelta(days=14)
    recent = df[df['date'] >= two_weeks_ago]
    
    if len(recent) < 2:
        return False, "Need more recent weigh-ins", current_target_cals
        
    # Simple rate calculation (start vs end of period)
    start_w = recent.iloc[0]['weight_kg']
    end_w = recent.iloc[-1]['weight_kg']
    days = (recent.iloc[-1]['date'] - recent.iloc[0]['date']).days
    
    if days < 7:
        return False, "Keep logging for a full week", current_target_cals
        
    loss_kg = start_w - end_w
    weekly_rate = (loss_kg / days) * 7
    
    if weekly_rate > 0.8:
        return True, f"Losing too fast ({weekly_rate:.2f}kg/week). Suggested: +150 kcal", current_target_cals + 150
    elif weekly_rate < 0.25 and weekly_rate > -0.2: # Stalled or gaining slightly
        return True, f"Loss stalled ({weekly_rate:.2f}kg/week). Suggested: -150 kcal", current_target_cals - 150
        
    return False, f"Good rate ({weekly_rate:.2f}kg/week)", current_target_cals

# --- AI INTEGRATION ---
def analyze_food_with_gemini(food_input, api_key):
    if not api_key or "YOUR_API_KEY" in api_key:
        st.error("Please provide a valid API Key.")
        return None
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    prompt = f"""
    You are a nutritionist AI. Analyze the following food input string.
    
    Input: "{food_input}"

    CRITICAL INSTRUCTIONS:
    1. If the input contains MULTIPLE distinct items (e.g., "5 potatoes, 1 carrot, 6 broccoli florets"), you MUST calculate the nutritional content for ALL items combined.
    2. Do NOT just output the first item. SUM the calories and nutrients for every item listed.
    3. For 'food_name', create a summary title that includes the main components (e.g. "Potatoes, Carrots & Broccoli").
    4. For 'breakdown', provide a concise string detailing the macros for each individual component found in the input. 
       Format: "Item: Cals, Protein; Item2: Cals, Protein" (e.g., "5 Potatoes: 500kcal, 10g P; 1 Carrot: 30kcal, 0.5g P").
    
    Return ONLY a JSON with this structure (for the TOTAL combined meal):
    {{
        "food_name": "string", "calories": int, "protein": int, "carbs": int, "sugar": int, "fiber": int,
        "total_fats": int, "saturated_fat": int, "sodium": int,
        "vitamin_a": int, "vitamin_c": int, "vitamin_d": int, "calcium": int, "iron": int, 
        "potassium": int, "magnesium": int, "zinc": int,
        "breakdown": "string" 
    }}
    """
    try:
        response = model.generate_content(prompt)
        data = extract_json(response.text)
        if isinstance(data, list) and len(data) > 0: return data[0]
        return data
    except Exception as e:
        st.error(f"AI Error: {e}"); return None

def analyze_image_with_gemini(image_bytes, api_key):
    if not api_key: return None
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"""
    Analyze this food image.
    Tasks:
    1. Identify ingredients separately.
    2. Identify cooking method (fried, grilled, boiled) and factor into calories.
    3. Estimate portion size.
    4. Provide a Confidence Score (0-100) on how sure you are.
    
    Return JSON:
    {{
        "food_name": "string", "calories": int, "protein": int, "carbs": int, "sugar": int, "fiber": int,
        "total_fats": int, "saturated_fat": int, "sodium": int,
        "vitamin_a": int, "vitamin_c": int, "vitamin_d": int, "calcium": int, "iron": int, 
        "potassium": int, "magnesium": int, "zinc": int,
        "breakdown": "string (e.g. 'Chicken (Grilled): 200kcal; Rice (Boiled): 150kcal')",
        "confidence_score": int
    }}
    """
    try:
        response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_bytes}])
        data = extract_json(response.text)
        return data[0] if isinstance(data, list) and data else data
    except Exception as e:
        st.error(f"AI Error: {e}"); return None

def analyze_daily_timeline(day_logs, targets, api_key):
    if not api_key: return "API Key missing."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    
    logs_text = "\n".join([f"- {l['food_name']}: {l['calories']}kcal, {l['protein']}g P" for l in day_logs])
    
    prompt = f"""
    Review this daily food log timeline. 
    Targets: {targets}
    Logs:
    {logs_text}
    
    For EACH meal, provide a 1-sentence quick evaluation (e.g., "Great protein source", "High sodium warning").
    Then provide a summary of the day's balance.
    """
    try:
        return model.generate_content(prompt).text
    except: return "Could not generate timeline analysis."

def get_weekly_insights(week_data, api_key):
    if not api_key: return "API Key missing."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    prompt = f"""
    Analyze these weekly food logs.
    Data: {week_data}
    
    Identify:
    1. Eating patterns (time of day, heavy dinners?)
    2. Specific foods eaten most often.
    3. Nutrient deficiencies (low fiber days?).
    4. Suggestions for next week.
    """
    try:
        return model.generate_content(prompt).text
    except: return "Analysis failed."

# --- ICONS & STYLING ---
def load_assets():
    st.markdown("""
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0" />
        <style>
            .icon { font-family: 'Material Symbols Rounded'; font-size: 24px; vertical-align: middle; }
            .big-icon { font-size: 28px; }
            .custom-bar-bg { background-color: #e0e0e0; border-radius: 8px; height: 20px; width: 100%; margin-top: 5px; }
            .custom-bar-fill { height: 100%; border-radius: 8px; transition: width 0.5s ease-in-out; }
            .suggestion-btn { margin: 2px; }
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
    
    st.title("AI Body Recomposition Tracker")
    
    # --- SIDEBAR ---
    with st.sidebar:
        st.header("Settings")
        if API_KEY == "YOUR_API_KEY_HERE":
            active_api_key = st.text_input("Enter Gemini API Key", type="password")
        else:
            active_api_key = API_KEY

        profile = dm.get_user_profile()
        
        # Defaults
        p_h, p_w, p_bf = 175.0, 70.0, 20.0
        p_act, p_goal, p_diet = "Sedentary", "Maintain / Recomp", "Balanced"
        
        if profile:
            p_h = profile.get('height_cm', 175.0)
            p_w = profile.get('weight_kg', 70.0)
            p_bf = profile.get('bf_percent', 20.0)
            p_act = profile.get('activity_level', "Sedentary")
            p_goal = profile.get('goal', "Maintain / Recomp")
            p_diet = profile.get('diet_type', "Balanced")

        st.divider()
        st.header("User Profile")
        with st.form("profile_form"):
            weight = st.number_input("Weight (kg)", value=float(p_w))
            height = st.number_input("Height (cm)", value=float(p_h))
            bf = st.number_input("Body Fat %", value=float(p_bf))
            
            act_opts = ["Sedentary", "Lightly Active", "Moderately Active", "Very Active"]
            try: act_idx = act_opts.index(p_act)
            except: act_idx = 0
            activity = st.selectbox("Activity Level", act_opts, index=act_idx)
            
            goal_opts = ["Maintain / Recomp", "Lose Weight", "Gain Muscle"]
            try: goal_idx = goal_opts.index(p_goal)
            except: goal_idx = 0
            goal = st.selectbox("Primary Goal", goal_opts, index=goal_idx)
            
            diet_opts = ["Balanced", "High Protein", "Keto"]
            try: diet_idx = diet_opts.index(p_diet)
            except: diet_idx = 0
            diet_type = st.selectbox("Diet Preference", diet_opts, index=diet_idx)
            
            if st.form_submit_button("Update Targets"):
                cals, prot, carbs, fats = calculate_macros(weight, height, bf, activity, goal, diet_type)
                user_data = {
                    'height_cm': height, 'weight_kg': weight, 'bf_percent': bf, 
                    'activity_level': activity, 'goal': goal, 'diet_type': diet_type,
                    'target_calories': cals, 'target_protein': prot, 
                    'target_carbs': carbs, 'target_fats': fats
                }
                dm.update_user_profile(user_data)
                st.toast("Targets Updated!")
                st.rerun()

    # --- LOAD USER DATA ---
    profile = dm.get_user_profile()
    if not profile:
        st.info("Please set profile in sidebar to begin.")
        return
        
    base_cals = profile.get('target_calories', 2000)
    t_prot = profile.get('target_protein', 150)
    t_carbs = profile.get('target_carbs', 200)
    t_fats = profile.get('target_fats', 60)
    user_goal = profile.get('goal', "Maintain")

    today = datetime.now().strftime("%Y-%m-%d")
    daily_target_cals = base_cals 

    # --- TABS ---
    tab1, tab2, tab3 = st.tabs(["Smart Log Engine", "Weekly Analytics", "Vision & Scan"])

    # --- TAB 1: SMART LOGGING ENGINE ---
    with tab1:
        c_date, _ = st.columns([1, 4])
        with c_date:
            view_date_obj = st.date_input("Tracking Date", value=datetime.now())
            view_date = view_date_obj.strftime("%Y-%m-%d")

        # 1A. SMART SUGGESTIONS (Templates, Frequent, Recent)
        with st.expander("⚡ Smart Suggestions (Templates & Frequent)", expanded=True):
            templates = dm.get_templates()
            # Mocking frequent/recent logic for simplicity - normally you'd query logs group by name
            recent_logs = dm.get_logs_history((datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"))
            recent_names = list(set([r['food_name'] for r in recent_logs]))[:5]

            col_sug1, col_sug2 = st.columns(2)
            with col_sug1:
                st.markdown("**My Templates**")
                if templates:
                    for t in templates:
                        if st.button(f"📄 {t['name']}", key=f"tpl_{t['id']}"):
                            food_data = json.loads(t['food_items_json'])
                            # Log it
                            dm.add_food_log({
                                'date': view_date, 'food_name': t['name'], 'amount_desc': "Template",
                                'calories': t['total_calories'], 'protein': t['total_protein'],
                                **food_data # Unpack rest
                            })
                            st.rerun()
                else:
                    st.caption("No templates yet. Save a meal as template after logging.")

            with col_sug2:
                st.markdown("**Recent / Frequent**")
                if recent_names:
                    for name in recent_names:
                        if st.button(f"🕒 {name}", key=f"rec_{name}"):
                            # Quick re-add (needs re-analysis or fetching old data - keeping simple re-analyze here)
                             with st.spinner("Re-analyzing..."):
                                data = analyze_food_with_gemini(name, active_api_key)
                                if data:
                                    data['date'] = view_date
                                    data['amount_desc'] = "Quick Add"
                                    data['note'] = data.get('breakdown', '')
                                    dm.add_food_log(data)
                                    st.rerun()
                else:
                    st.caption("Log more meals to see suggestions.")

        # 2. MULTI-SOURCE INPUT
        st.divider()
        st.markdown("### 🍽️ Add a Meal")
        input_method = st.radio("Input Method", ["⌨️ Text Search", "🗣️ Describe Meal"], horizontal=True, label_visibility="collapsed")
        
        f_name = st.text_input("What did you eat?", placeholder="e.g. 2 Eggs and Toast")
        
        if st.button("Log Meal", type="primary"):
            if not f_name: st.warning("Describe food first.")
            else:
                with st.spinner("Analyzing..."):
                    data = analyze_food_with_gemini(f_name, active_api_key) 
                    if data:
                        log_entry = {
                            'date': view_date, 'food_name': data['food_name'], 'amount_desc': f_name,
                            'calories': data['calories'], 'protein': data['protein'], 
                            'carbs': data['carbs'], 'fats': data['total_fats'], 
                            'fiber': data['fiber'], 'sugar': data['sugar'], 'sodium': data['sodium'],
                            'saturated_fat': data['saturated_fat'], 'vitamin_a': data['vitamin_a'],
                            'vitamin_c': data['vitamin_c'], 'vitamin_d': data['vitamin_d'],
                            'calcium': data['calcium'], 'iron': data['iron'], 'potassium': data['potassium'],
                            'magnesium': data['magnesium'], 'zinc': data['zinc'], 
                            'note': data.get('breakdown', '')
                        }
                        dm.add_food_log(log_entry)
                        # Set flag to show "Save Template"
                        st.session_state['last_logged'] = data
                        st.rerun()
                    else: st.error("Analysis failed.")

        # SAVE AS TEMPLATE OPTION
        if 'last_logged' in st.session_state:
            last = st.session_state['last_logged']
            st.info(f"Logged: {last['food_name']}")
            if st.button("💾 Save this meal as Template"):
                dm.add_template(last['food_name'], last)
                st.success("Template Saved!")
                del st.session_state['last_logged']

        # DAILY LOGS (SUMMARY)
        st.divider()
        logs = dm.get_logs_for_date(view_date)
        if logs:
            # Calc Totals
            c_cal = sum(l.get('calories', 0) for l in logs)
            c_prot = sum(l.get('protein', 0) for l in logs)
            # Metrics
            m1, m2 = st.columns(2)
            with m1: render_big_metric("Calories", "local_fire_department", c_cal, daily_target_cals, "kcal", "#ff5722")
            with m2: render_big_metric("Protein", "fitness_center", c_prot, t_prot, "g", "#4caf50")

            for log in reversed(logs):
                with st.container(border=True):
                    c1, c2 = st.columns([5,1])
                    with c1: st.markdown(f"**{log['food_name']}**")
                    with c2: 
                        if st.button("✖", key=f"d_{log['id']}"):
                            dm.delete_food_log(log['id'])
                            st.rerun()
                    st.markdown(f"""
                    <div style='display:flex; gap:20px; margin:5px 0;'>
                        <span style='color:#ff5722; font-weight:bold;'>{log['calories']} kcal</span>
                        <span style='color:#4caf50; font-weight:bold;'>{log['protein']}g P</span>
                    </div>
                    """, unsafe_allow_html=True)
                    if log.get('note'): st.caption(f"{log['note']}")

    # --- TAB 2: WEEKLY ANALYTICS & DASHBOARD ---
    with tab2:
        # 4. DASHBOARD TABS
        an_tab1, an_tab2, an_tab3, an_tab4 = st.tabs(["📈 Nutrition Trends", "⚖️ Weight Trends", "🤖 AI Insights", "💬 Timeline"])
        
        # Get Data
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_logs = dm.get_logs_history(week_ago)
        df_week = pd.DataFrame(week_logs) if week_logs else pd.DataFrame()

        with an_tab1:
            if not df_week.empty:
                daily_sums = df_week.groupby('date')[['calories', 'protein', 'carbs', 'fats', 'fiber', 'sodium']].sum().reset_index()
                
                st.markdown("##### Calories vs Target")
                st.bar_chart(daily_sums.set_index('date')['calories'])
                
                st.markdown("##### Macros")
                st.line_chart(daily_sums.set_index('date')[['protein', 'carbs', 'fats']])
                
                st.markdown("##### Micros (Fiber & Sodium)")
                st.line_chart(daily_sums.set_index('date')[['fiber', 'sodium']])
            else:
                st.info("Not enough data for trends.")

        with an_tab2:
            st.markdown("##### Weight Tracker & Auto-Adjust")
            # Get body stats
            stats = dm.get_body_stats_history()
            if stats:
                df_stats = pd.DataFrame(stats)
                st.line_chart(df_stats.set_index('date')['weight_kg'])
                
                # MACRO AUTO ADJUST LOGIC
                latest_w = df_stats.iloc[-1]['weight_kg']
                should_adjust, msg, new_cal = check_macro_auto_adjust(stats, base_cals)
                
                st.metric("Current Weight", f"{latest_w} kg")
                
                if should_adjust:
                    st.warning(f"⚠️ {msg}")
                    if st.button(f"Auto-Adjust Targets to {new_cal} kcal"):
                         # Update profile
                         user_data = {'target_calories': new_cal} # Minimal update for demo
                         # Ideally fetch full profile, update cals, save back
                         full_p = dm.get_user_profile()
                         full_p['target_calories'] = new_cal
                         dm.update_user_profile(full_p)
                         st.success("Macros Adjusted!")
                         st.rerun()
                else:
                    st.success(f"✅ {msg}")
            else:
                st.info("Log weight in sidebar to see trends.")

        with an_tab3:
            st.markdown("##### 🧠 AI Weekly Summary")
            if not df_week.empty and st.button("Generate Weekly Report"):
                with st.spinner("Analyzing patterns..."):
                    summary = df_week.to_string()
                    insights = get_weekly_insights(summary, active_api_key)
                    st.markdown(insights)

        with an_tab4:
            # 5. NUTRITION TIMELINE (AI Chat View)
            st.markdown("##### 💬 Daily AI Coach")
            # Show today's logs in chat style
            today_logs = dm.get_logs_for_date(today)
            if today_logs:
                for l in today_logs:
                    with st.chat_message("user"):
                        st.markdown(f"**{l['food_name']}**")
                        st.caption(f"{l['calories']} kcal | {l['protein']}g Protein")
                    
                    # Simulating quick AI comment (In real app, store this to avoid re-generating cost)
                    # For now, basic logic or placeholder
                    score = min(10, int((l['protein'] / (l['calories']+1)) * 100)) # Crude score
                    with st.chat_message("assistant"):
                        st.markdown(f"Meal Score: **{score}/10**.")
                        if l['sodium'] > 800: st.markdown("⚠️ High sodium here.")
                        if l['protein'] > 30: st.markdown("💪 Excellent protein boost!")
            else:
                st.info("Log meals today to see the timeline.")

    # --- TAB 3: VISION & SCAN ---
    with tab3:
        st.markdown("### 📸 AI Plate Recognition v2")
        
        img_file = st.camera_input("Snap your meal")
        if img_file:
            bytes_data = img_file.getvalue()
            st.image(bytes_data, caption="Review", width=300)
            
            if st.button("Analyze Photo", type="primary"):
                with st.spinner("AI Identifying ingredients & cooking methods..."):
                    data = analyze_image_with_gemini(bytes_data, active_api_key)
                    if data:
                        # Edit before save
                        with st.expander("Edit Details", expanded=True):
                            col_e1, col_e2 = st.columns(2)
                            with col_e1:
                                new_name = st.text_input("Name", data.get('food_name'))
                                new_cal = st.number_input("Calories", value=data.get('calories', 0))
                            with col_e2:
                                new_prot = st.number_input("Protein", value=data.get('protein', 0))
                                conf = data.get('confidence_score', 0)
                                st.caption(f"AI Confidence: {conf}%")

                        if st.button("Confirm & Log"):
                            # Update data with edits
                            data['food_name'] = new_name
                            data['calories'] = new_cal
                            data['protein'] = new_prot
                            
                            log_entry = {
                                'date': today, 'food_name': data['food_name'], 'amount_desc': "Photo Log v2",
                                'calories': data['calories'], 'protein': data['protein'], 
                                'carbs': data.get('carbs', 0), 'fats': data.get('total_fats', 0), 
                                'fiber': data.get('fiber', 0), 'sugar': data.get('sugar', 0), 'sodium': data.get('sodium', 0),
                                'saturated_fat': data.get('saturated_fat', 0), 'vitamin_a': data.get('vitamin_a', 0),
                                'vitamin_c': data.get('vitamin_c', 0), 'vitamin_d': data.get('vitamin_d', 0),
                                'calcium': data.get('calcium', 0), 'iron': data.get('iron', 0), 'potassium': data.get('potassium', 0),
                                'magnesium': data.get('magnesium', 0), 'zinc': data.get('zinc', 0), 
                                'note': data.get('breakdown', '')
                            }
                            dm.add_food_log(log_entry)
                            st.success("Logged!")
                    else:
                        st.error("Vision analysis failed.")

if __name__ == "__main__":
    main()
