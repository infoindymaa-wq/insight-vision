import os
import time
import pickle
import feedparser
import requests
import random
import json
import sys
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime, timedelta, timezone
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv

# Try loading .env (only for local)
load_dotenv()

# Configuration - CHECKING BOTH SYSTEM AND ENV
BLOG_ID = os.environ.get("BLOG_ID") or os.getenv("BLOG_ID")
GROQ_API_KEY_1 = os.environ.get("GROQ_API_KEY_1") or os.getenv("GROQ_API_KEY_1")
GROQ_API_KEY_2 = os.environ.get("GROQ_API_KEY_2") or os.getenv("GROQ_API_KEY_2")

API_KEYS = [GROQ_API_KEY_1, GROQ_API_KEY_2]
RSS_FEED_URL = "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
POSTED_NEWS_FILE = "posted_news.txt"
KEY_INDEX_FILE = "last_key_index.txt"
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

print("--- ENVIRONMENT DIAGNOSTIC ---")
print(f"IS_GITHUB_ACTIONS: {IS_GITHUB_ACTIONS}")
print(f"BLOG_ID Status: {'FOUND' if (BLOG_ID and len(BLOG_ID) > 2) else 'NOT FOUND OR EMPTY'}")
print(f"GROQ_API_KEY_1 Status: {'FOUND' if (GROQ_API_KEY_1 and len(GROQ_API_KEY_1) > 2) else 'NOT FOUND OR EMPTY'}")
print(f"GROQ_API_KEY_2 Status: {'FOUND' if (GROQ_API_KEY_2 and len(GROQ_API_KEY_2) > 2) else 'NOT FOUND OR EMPTY'}")
print(f"Available Env Vars: {[k for k in os.environ.keys() if 'GROQ' in k or 'BLOG' in k]}")
print("------------------------------")

def get_current_key():
    index = 0
    if os.path.exists(KEY_INDEX_FILE):
        with open(KEY_INDEX_FILE, "r") as f:
            try: index = int(f.read().strip())
            except: index = 0
    
    # Filter out empty keys
    valid_keys = [k for k in API_KEYS if k and len(k) > 5]
    if not valid_keys: return None
    
    current_key = valid_keys[index % len(valid_keys)]
    next_index = (index + 1) % len(valid_keys)
    with open(KEY_INDEX_FILE, "w") as f:
        f.write(str(next_index))
    return current_key

def get_blogger_service():
    print("DEBUG: Initializing Blogger Service...")
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("DEBUG: Refreshing token...")
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"ERROR: Token refresh failed: {e}")
                sys.exit(1)
        else:
            print("ERROR: No valid token file found or token is expired without refresh capability.")
            sys.exit(1)
            
    return build('blogger', 'v3', credentials=creds)

def is_valid_news_image(url):
    if not url: return False
    url_lower = url.lower()
    if any(x in url_lower for x in ["googleusercontent.com", "google.com", "gstatic.com"]): return False
    logo_keywords = ['logo', 'icon', 'favicon', 'placeholder', 'header', 'nav', 'advertisement', 'subscribe', 'banner', 'button', 'thumb', 'avatar']
    if any(word in url_lower for word in logo_keywords): return False
    if not any(ext in url_lower for ext in ['.jpg', '.jpeg', '.png', '.webp']): return False
    return True

def get_web_search_image(headline, api_key):
    client = Groq(api_key=api_key)
    try:
        keyword_prompt = f"Give a 3-word English keyword for a news photo: {headline}. Only keyword."
        keyword_res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": keyword_prompt}],
            max_tokens=15
        )
        search_query = keyword_res.choices[0].message.content.strip().strip('"')
        print(f"DEBUG: Search Query: {search_query}")
        search_url = f"https://www.bing.com/images/search?q={search_query.replace(' ', '+')}&qft=+filterui:imagesize-large+filterui:aspect-wide&form=IRFLTR&first=1"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(search_url, headers=headers, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        img_links = []
        for a in soup.find_all("a", class_="iusc"):
            try:
                murl = json.loads(a.get("m", "{}")).get("murl")
                if murl and is_valid_news_image(murl): img_links.append(murl)
            except: continue
        return random.choice(img_links[:3]) if img_links else None
    except Exception as e:
        print(f"DEBUG: Image Search Error: {e}")
        return None

def generate_unique_headline(original_title, api_key):
    client = Groq(api_key=api_key)
    try:
        prompt = f"Rewrite this news headline to be catchy, professional, and SEO-friendly in English: {original_title}. Return ONLY the new headline."
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=50
        )
        return response.choices[0].message.content.strip().strip('"')
    except Exception as e:
        print(f"DEBUG: Headline Gen Error: {e}")
        return original_title

def generate_ai_content(headline, image_url, api_key):
    client = Groq(api_key=api_key)
    image_html = f'<div style="text-align:center;"><img src="{image_url}" style="max-width:100%; border-radius:10px; margin-bottom:20px;"></div>' if image_url else ""
    prompt = f"Topic: {headline}\nTask: Write a detailed, 800-word professional news article in English. Structure: Introduction, Deep Analysis, Impact, Conclusion. Format: Use HTML tags (<h2>, <p>, <b>). Strictly English."
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are a professional editor."}, {"role": "user", "content": prompt}],
            max_tokens=3000
        )
        return image_html + completion.choices[0].message.content
    except Exception as e:
        print(f"DEBUG: Content Gen Error: {e}")
        return None

def post_to_blogger(service, title, content):
    body = {"kind": "blogger#post", "title": title, "content": content}
    try:
        response = service.posts().insert(blogId=BLOG_ID, body=body).execute()
        print(f"SUCCESS: Post successful! URL: {response.get('url')}")
        return True
    except Exception as e:
        print(f"ERROR: Blogger Post Failed: {e}")
        return False

def main():
    print("--- STARTING BOT ---")
    if not BLOG_ID or len(BLOG_ID) < 5:
        print("CRITICAL ERROR: BLOG_ID is missing or empty.")
        sys.exit(1)
    
    if not GROQ_API_KEY_1 or len(GROQ_API_KEY_1) < 5:
        print("CRITICAL ERROR: GROQ_API_KEY_1 is missing or empty.")
        sys.exit(1)

    service = get_blogger_service()
    feed = feedparser.parse(RSS_FEED_URL)
    
    if not feed.entries:
        print("ERROR: RSS Feed is empty.")
        sys.exit(1)

    news_to_process = feed.entries[:5] 

    for news in news_to_process:
        print(f"\n--- Processing: {news.title} ---")
        api_key = get_current_key()
        
        headline = generate_unique_headline(news.title, api_key)
        image_url = get_web_search_image(headline, api_key)
        article = generate_ai_content(headline, image_url, api_key)
        
        if article and post_to_blogger(service, headline, article):
            if os.path.exists(POSTED_NEWS_FILE):
                with open(POSTED_NEWS_FILE, "a", encoding="utf-8") as f:
                    f.write(news.title + "\n")
            
            if IS_GITHUB_ACTIONS:
                print("DEBUG: Single post completed. Exiting.")
                sys.exit(0)
            
            print("DEBUG: Waiting 6 minutes...")
            time.sleep(360)

if __name__ == "__main__":
    main()
