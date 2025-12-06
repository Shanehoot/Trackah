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
        
        # Templates Table
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
        # Generate unique ID
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
            # Firestore doesn't inherently order by insertion without a timestamp field in the query
            # Relying on date filtering
            docs = self.db.collection('food_logs').where('date', '>=', start_date_str).stream()
            return [doc.to_dict() for doc in docs]
        else:
            conn = sqlite3.connect(self.sqlite_db)
            # Ordered by ID DESC ensures newest logs come first
            df = pd.read_sql_query(f"SELECT * FROM food_logs WHERE date >= '{start_date_str}' ORDER BY id DESC", conn)
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
            stats.sort(key=lambda x: x['date'], reverse=True)
            return stats[0]
        return None

    # --- TEMPLATES METHODS ---
    def add_template(self, name, food_data, default_type="None"):
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
    If multiple items are present, sum their nutritional values together.
    
    Return JSON:
    {{
        "food_name": "string", "calories": int, "protein": int, "carbs": int, "sugar": int, "fiber": int,
        "total_fats": int, "saturated_fat": int, "sodium": int,
        "vitamin_a": int, "vitamin_c": int, "vitamin_d": int, "calcium": int, "iron": int, 
        "potassium": int, "magnesium": int, "zinc": int,
        "breakdown": "Concise string listing macros per visual component (e.g. 'Steak: 400cal, 40g P; Salad: 50cal, 1g P')"
    }}
    """
    try:
        response = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": image_bytes}])
        data = extract_json(response.text)
        return data[0] if isinstance(data, list) and data else data
    except Exception as e:
        st.error(f"AI Error: {e}"); return None

def analyze_planned_meal(planned_food, current_status, targets, api_key):
    if not api_key: return "API Key missing."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    prompt = f"""
    Coach user on planned meal: "{planned_food}".
    Targets: {targets}. Current Status: {current_status}.
    Tasks: 1. Budget check. 2. Micro/Macro check. 3. Suggestions.
    """
    try:
        return model.generate_content(prompt).text
    except Exception as e: return str(e)

def get_weekly_analysis(week_data, averages, targets, goal, api_key):
    if not api_key: return "API Key missing."
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-2.5-flash-preview-09-2025')
    prompt = f"""
    Weekly analysis for "{goal}". Avgs: {averages}. Targets: {targets}. Logs: {week_data}.
    Provide: 1. Adherence summary. 2. Wins/Improvements. 3. Tip.
    """
    try:
        return model.generate_content(prompt).text
    except Exception as e: return str(e)

# --- ICONS & STYLING ---
def load_assets():
    st.markdown("""
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0" />
        <style>
            .icon { font-family: 'Material Symbols Rounded'; font-size: 24px; vertical-align: middle; }
            .big-icon { font-size: 28px; }
            .custom-bar-bg { background-color: #e0e0e0; border-radius: 8px; height: 20px; width: 100%; margin-top: 5px; }
            .custom-bar-fill { height: 100%; border-radius: 8px; transition: width 0.5s ease-in-out; }
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
    
    # --- STORAGE STATUS INDICATOR ---
    if dm.use_firestore:
        pass # Silent success
    else:
        st.warning("🟠 Using Local Storage (Data wipes on restart)")
        
        # DEBUG INFO FOR USER
        with st.expander("Troubleshooting Connection", expanded=False):
            st.write("If you expect to be connected to the cloud, check these:")
            
            if not HAS_FIRESTORE_LIB:
                st.error("❌ Missing Libraries: `google-cloud-firestore` or `google-auth` not installed.")
                if IMPORT_ERROR:
                    st.code(IMPORT_ERROR, language="text")
            else:
                st.success("✅ Libraries Installed")
                
            if "gcp_service_account" not in st.secrets:
                st.error("❌ Missing Secrets: `[gcp_service_account]` section not found in secrets.toml")
            else:
                st.success("✅ Secrets Found")
                
            if dm.connection_error:
                st.error(f"❌ Database Error: {dm.connection_error}")

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
    tab1, tab2, tab3 = st.tabs(["Daily Tracker", "AI Coach", "Vision & Scan"])

    # --- TAB 1: DAILY TRACKER ---
    with tab1:
        c_date, _ = st.columns([1, 4])
        with c_date:
            view_date_obj = st.date_input("Tracking Date", value=datetime.now())
            view_date = view_date_obj.strftime("%Y-%m-%d")

        # 1A. Smart Suggestions (Templates + Recent)
        with st.expander("⚡ Smart Suggestions (Templates & Recent)", expanded=True):
            templates = dm.get_templates()
            # Fetch recent logs, sort by date descending to get latest
            recent_logs = dm.get_logs_history((datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d"))
            recent_logs.sort(key=lambda x: x.get('date', ''), reverse=True) # Sort in memory
            
            # Filter for unique names, keeping top 3 most recent
            recent_names = []
            for r in recent_logs:
                if r['food_name'] not in recent_names:
                    recent_names.append(r['food_name'])
                if len(recent_names) >= 3:
                    break

            col_sug1, col_sug2 = st.columns(2)
            with col_sug1:
                st.markdown("**My Templates**")
                if templates:
                    for t in templates:
                        c_t1, c_t2 = st.columns([4, 1])
                        with c_t1:
                            if st.button(f"📄 {t['name']}", key=f"tpl_{t['id']}"):
                                food_data = json.loads(t['food_items_json'])
                                dm.add_food_log({
                                    'date': view_date, 'food_name': t['name'], 'amount_desc': "Template",
                                    'calories': t['total_calories'], 'protein': t['total_protein'],
                                    **food_data 
                                })
                                st.rerun()
                        with c_t2:
                            if st.button("🗑️", key=f"del_tpl_{t['id']}"):
                                dm.delete_template(t['id'])
                                st.rerun()
                else: st.caption("No templates yet.")
            with col_sug2:
                st.markdown("**Recent**")
                if recent_names:
                    for name in recent_names:
                        if st.button(f"🕒 {name}", key=f"rec_{name}"):
                             with st.spinner("Re-analyzing..."):
                                data = analyze_food_with_gemini(name, active_api_key)
                                if data:
                                    data['date'] = view_date; data['amount_desc'] = "Quick Add"; data['note'] = data.get('breakdown', '')
                                    dm.add_food_log(data)
                                    st.rerun()
                else: st.caption("Log more meals.")

        col1, col2 = st.columns([1.6, 1])
        with col1:
            st.subheader("Daily Overview")
            logs = dm.get_logs_for_date(view_date)
            
            # Aggregate Manually
            c_cal = sum(l.get('calories', 0) for l in logs)
            c_prot = sum(l.get('protein', 0) for l in logs)
            c_carb = sum(l.get('carbs', 0) for l in logs)
            c_fat = sum(l.get('fats', 0) for l in logs)
            c_fiber = sum(l.get('fiber', 0) for l in logs)
            c_sugar = sum(l.get('sugar', 0) for l in logs)
            c_sodium = sum(l.get('sodium', 0) for l in logs)
            c_sat_fat = sum(l.get('saturated_fat', 0) for l in logs)
            
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
            
            c_vit_a = sum(l.get('vitamin_a', 0) for l in logs)
            c_vit_c = sum(l.get('vitamin_c', 0) for l in logs)
            c_vit_d = sum(l.get('vitamin_d', 0) for l in logs)
            c_calc = sum(l.get('calcium', 0) for l in logs)
            c_iron = sum(l.get('iron', 0) for l in logs)
            c_pot = sum(l.get('potassium', 0) for l in logs)
            c_mag = sum(l.get('magnesium', 0) for l in logs)
            c_zinc = sum(l.get('zinc', 0) for l in logs)
            
            m1, m2, m3, m4 = st.columns(4)
            with m1: render_micro_metric("Vit A", "visibility", c_vit_a, "µg", "#FF9800")
            with m2: render_micro_metric("Vit C", "nutrition", c_vit_c, "mg", "#FFEB3B")
            with m3: render_micro_metric("Vit D", "sunny", c_vit_d, "µg", "#FFC107")
            with m4: render_micro_metric("Calc.", "egg", c_calc, "mg", "#F5F5F5")

            m5, m6, m7, m8 = st.columns(4)
            with m5: render_micro_metric("Iron", "hexagon", c_iron, "mg", "#795548")
            with m6: render_micro_metric("Potass.", "bolt", c_pot, "mg", "#673AB7")
            with m7: render_micro_metric("Magnes.", "spa", c_mag, "mg", "#009688")
            with m8: render_micro_metric("Zinc", "science", c_zinc, "mg", "#607D8B")

            st.divider()
            with st.container(border=True):
                st.markdown(f"#### <span class='icon'>add_circle</span> Add Meal", unsafe_allow_html=True)
                f_name = st.text_input("Describe your meal", placeholder="e.g., Double cheeseburger no bun")
                # REMOVED NOTE INPUT HERE
                if st.button("Log Meal", type="primary"):
                    if not f_name: st.warning("Describe food first.")
                    else:
                        with st.spinner("Analyzing..."):
                            data = analyze_food_with_gemini(f_name, active_api_key) # Pass empty string for note
                            if data:
                                # Prepare data dictionary
                                log_entry = {
                                    'date': view_date, 'food_name': data['food_name'], 'amount_desc': f_name,
                                    'calories': data['calories'], 'protein': data['protein'], 
                                    'carbs': data['carbs'], 'fats': data['total_fats'], 
                                    'fiber': data['fiber'], 'sugar': data['sugar'], 'sodium': data['sodium'],
                                    'saturated_fat': data['saturated_fat'], 'vitamin_a': data['vitamin_a'],
                                    'vitamin_c': data['vitamin_c'], 'vitamin_d': data['vitamin_d'],
                                    'calcium': data['calcium'], 'iron': data['iron'], 'potassium': data['potassium'],
                                    'magnesium': data['magnesium'], 'zinc': data['zinc'], 
                                    'note': data.get('breakdown', '') # Use breakdown as note
                                }
                                dm.add_food_log(log_entry)
                                st.session_state['last_logged'] = data
                                st.rerun()
                            else: st.error("Analysis failed.")
            
            # Save Template Option
            if 'last_logged' in st.session_state:
                last = st.session_state['last_logged']
                if st.button(f"💾 Save '{last['food_name']}' as Template"):
                    dm.add_template(last['food_name'], last)
                    st.success("Saved!"); del st.session_state['last_logged']; st.rerun()

        with col2:
            st.subheader("Logs")
            logs = dm.get_logs_for_date(view_date)
            # logs already fetched
            if logs:
                for log in reversed(logs): # Show newest first
                    with st.container(border=True):
                        c1, c2 = st.columns([5,1])
                        with c1: st.markdown(f"**{log['food_name']}**")
                        with c2: 
                            if st.button("✖", key=f"d_{log['id']}"):
                                dm.delete_food_log(log['id'])
                                st.rerun()
                        st.markdown(f"""
                        <div style='display:flex; gap:20px; margin:10px 0;'>
                            <span style='color:#4caf50; font-weight:bold; font-size: 1.1em;'><span class='icon'>fitness_center</span>{log['protein']}g</span>
                            <span style='color:#ff5722; font-weight:bold; font-size: 1.1em;'><span class='icon'>local_fire_department</span>{log['calories']}</span>
                        </div>
                        <div style='font-size:0.85em; color:#555;'>C:{log.get('carbs', 0)}g F:{log.get('fats', 0)}g (Sat:{log.get('saturated_fat',0)}g) Fib:{log.get('fiber', 0)}g Sug:{log.get('sugar', 0)}g Sod:{log.get('sodium', 0)}mg</div>
                        """, unsafe_allow_html=True)
                        
                        # SHOW BREAKDOWN AS NOTE
                        if log.get('note'):
                             st.caption(f"📝 {log['note']}")

            else: st.info("No meals.")
            
            if st.button("Clear Day", type="secondary"):
                dm.delete_day_logs(view_date)
                st.rerun()

    # --- TAB 2: AI COACH ---
    with tab2:
        st.markdown("### <span class='icon'>smart_toy</span> AI Nutrition Coach", unsafe_allow_html=True)
        
        # 1. TOP: MEAL ANALYSIS & CURRENT STATUS
        today_logs = dm.get_logs_for_date(today)
        cur_status = {
            'cals': sum(l['calories'] for l in today_logs),
            'prot': sum(l['protein'] for l in today_logs),
            'fiber': sum(l['fiber'] for l in today_logs),
            'sugar': sum(l['sugar'] for l in today_logs),
            'sodium': sum(l['sodium'] for l in today_logs)
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
        
        # Get all logs for stats
        all_logs = dm.get_logs_history("2020-01-01") # Arbitrary start date
        
        if all_logs:
            df = pd.DataFrame(all_logs)
            # Group by date to get daily sums
            daily_sums = df.groupby('date')[['calories', 'protein', 'carbs', 'fats']].sum()
            
            avg_cals = daily_sums['calories'].mean()
            avg_prot = daily_sums['protein'].mean()
            avg_carbs = daily_sums['carbs'].mean()
            avg_fats = daily_sums['fats'].mean()
            
            col_a, col_b, col_c, col_d = st.columns(4)
            
            def diff_metric(col, label, val, target, unit):
                diff = val - target
                delta_str = f"{diff:+.0f} {unit}"
                col.metric(label, f"{val:.0f} {unit}", delta_str, delta_color="inverse" if label in ["Calories", "Carbs", "Fats"] else "normal")

            diff_metric(col_a, "Avg Calories", avg_cals, daily_target_cals, "")
            diff_metric(col_b, "Avg Protein", avg_prot, t_prot, "g")
            diff_metric(col_c, "Avg Carbs", avg_carbs, t_carbs, "g")
            diff_metric(col_d, "Avg Fats", avg_fats, t_fats, "g")
            
            hit_rate = (daily_sums['protein'] >= t_prot * 0.9).mean() * 100
            st.caption(f"You hit your protein goal **{hit_rate:.1f}%** of the days logged.")
            
        else:
            st.info("Log more meals to see your consistency stats.")

        st.divider()

        # 3. BOTTOM: WEEKLY REPORT & WINDOWS
        st.markdown("#### <span class='icon'>calendar_month</span> Weekly Report", unsafe_allow_html=True)
        
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_logs = dm.get_logs_history(week_ago)
        
        if week_logs:
            w_df = pd.DataFrame(week_logs)
            w_daily = w_df.groupby('date')[['calories', 'protein', 'carbs', 'fats']].sum()
            
            w_avg_cals = w_daily['calories'].mean()
            w_avg_prot = w_daily['protein'].mean()
            w_avg_carbs = w_daily['carbs'].mean()
            w_avg_fats = w_daily['fats'].mean()
            
            if st.button("Generate Weekly Analysis"):
                with st.spinner("Coach is reviewing your week..."):
                    avgs = {'cals': int(w_avg_cals), 'prot': int(w_avg_prot), 'carbs': int(w_avg_carbs), 'fats': int(w_avg_fats)}
                    summary_text = w_daily.to_string()
                    report = get_weekly_analysis(summary_text, avgs, targets, user_goal, active_api_key)
                    st.markdown(report)
            
            st.write("")
            st.markdown("##### Weekly Averages Summary")
            
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

    # --- TAB 3: VISION & SCAN ---
    with tab3:
        st.markdown("### 📸 Vision & Scan", unsafe_allow_html=True)
        
        scan_mode = st.radio("Mode", ["AI Plate Recognition", "Barcode Scanner"], horizontal=True)
        
        if scan_mode == "AI Plate Recognition":
            cam_col, review_col = st.columns([1, 1])
            
            with cam_col:
                img_file = st.camera_input("Snap your meal")
            
            with review_col:
                if img_file:
                    bytes_data = img_file.getvalue()
                    st.image(bytes_data, caption="Captured Image", use_column_width=True)
                    
                    if st.button("Analyze & Log Photo", type="primary"):
                        with st.spinner("Analyzing image..."):
                            data = analyze_image_with_gemini(bytes_data, active_api_key)
                            if data:
                                st.success(f"Identified: {data.get('food_name')}")
                                # Log to DB
                                log_entry = {
                                    'date': today, 'food_name': data['food_name'], 'amount_desc': "Photo Log",
                                    'calories': data['calories'], 'protein': data['protein'], 
                                    'carbs': data['carbs'], 'fats': data['total_fats'], 
                                    'fiber': data['fiber'], 'sugar': data['sugar'], 'sodium': data['sodium'],
                                    'saturated_fat': data['saturated_fat'], 'vitamin_a': data['vitamin_a'],
                                    'vitamin_c': data['vitamin_c'], 'vitamin_d': data['vitamin_d'],
                                    'calcium': data['calcium'], 'iron': data['iron'], 'potassium': data['potassium'],
                                    'magnesium': data['magnesium'], 'zinc': data['zinc'], 
                                    'note': data.get('breakdown', '') # Use breakdown as note
                                }
                                dm.add_food_log(log_entry)
                                st.toast("Meal logged successfully!")
                            else:
                                st.error("Could not analyze image.")
        else:
            st.info("Barcode Scanner Feature Coming Soon!")

if __name__ == "__main__":
    main()
