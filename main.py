import os
import time
import pickle
import feedparser
import requests
import random
import json
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime, timedelta, timezone
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_KEYS = [os.getenv("GROQ_API_KEY_1"), os.getenv("GROQ_API_KEY_2")]
BLOGGER_BLOG_ID = os.getenv("BLOG_ID") 
RSS_FEED_URL = "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
POSTED_NEWS_FILE = "posted_news.txt"
KEY_INDEX_FILE = "last_key_index.txt"
IS_GITHUB_ACTIONS = os.getenv("GITHUB_ACTIONS") == "true"

def get_current_key():
    index = 0
    if os.path.exists(KEY_INDEX_FILE):
        with open(KEY_INDEX_FILE, "r") as f:
            try: index = int(f.read().strip())
            except: index = 0
    current_key = API_KEYS[index]
    next_index = (index + 1) % len(API_KEYS)
    with open(KEY_INDEX_FILE, "w") as f:
        f.write(str(next_index))
    return current_key

def get_blogger_service():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                'client_secrets.json', ['https://www.googleapis.com/auth/blogger'])
            creds = flow.run_local_server(port=0)
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)
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
        if img_links: return random.choice(img_links[:3])
    except Exception as e: print(f"Image Error: {e}")
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
    except:
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
        print(f"Content Error: {e}")
        return None

def post_to_blogger(service, title, content):
    body = {"kind": "blogger#post", "title": title, "content": content}
    try:
        service.posts().insert(blogId=BLOGGER_BLOG_ID, body=body).execute()
        return True
    except Exception as e:
        print(f"Blogger Error: {e}")
        return False

def countdown(seconds):
    if IS_GITHUB_ACTIONS: return # No countdown in GitHub
    while seconds > 0:
        print(f"Next post in: {seconds//60:02d}:{seconds%60:02d}", end="\r")
        time.sleep(1)
        seconds -= 1
    print("\nStarting...")

def main():
    print("Starting AI News Bot (6-Minute Edition)...")
    service = get_blogger_service()

    while True:
        feed = feedparser.parse(RSS_FEED_URL)
        now = datetime.now(timezone.utc)
        four_hours_ago = now - timedelta(hours=4)

        posted_titles = []
        if os.path.exists(POSTED_NEWS_FILE):
            with open(POSTED_NEWS_FILE, "r", encoding="utf-8") as f:
                posted_titles = f.read().splitlines()

        news_to_process = []
        for entry in feed.entries:
            if entry.title not in posted_titles:
                news_to_process.append({"original_title": entry.title, "link": entry.link})

        if not news_to_process:
            print("No new news. Waiting 6 minutes...")
            if IS_GITHUB_ACTIONS: break # Exit in GitHub Actions
            countdown(360)
            continue

        for news in news_to_process:
            print(f"DEBUG: Checking duplicate for: {news['original_title']}")
            if os.path.exists(POSTED_NEWS_FILE):
                with open(POSTED_NEWS_FILE, "r", encoding="utf-8") as f:
                    if news['original_title'] in f.read().splitlines(): 
                        print(f"DEBUG: Already posted, skipping.")
                        continue
            else:
                # Create the file if it doesn't exist
                open(POSTED_NEWS_FILE, "w").close()

            print(f"DEBUG: Processing New Topic: {news['original_title']}")
            api_key = get_current_key()
            
            print("DEBUG: Generating Headline...")
            headline = generate_unique_headline(news['original_title'], api_key)
            print(f"DEBUG: AI Headline: {headline}")

            print("DEBUG: Searching for Image...")
            image_url = get_web_search_image(headline, api_key)
            print(f"DEBUG: Image URL found: {image_url}")

            print("DEBUG: Generating Article Body...")
            article = generate_ai_content(headline, image_url, api_key)
            
            if article:
                print("DEBUG: Attempting to post to Blogger...")
                if post_to_blogger(service, headline, article):
                    with open(POSTED_NEWS_FILE, "a", encoding="utf-8") as f:
                        f.write(news['original_title'] + "\n")
                    print("DEBUG: SUCCESSFULLY POSTED!")
                    if IS_GITHUB_ACTIONS: 
                        print("DEBUG: GitHub Action single-run limit reached. Exiting.")
                        return
                    countdown(360)
                else:
                    print("DEBUG: Blogger Post Failed.")
            else:
                print("DEBUG: Article Generation Failed (AI empty).")
                time.sleep(60)
        
        if IS_GITHUB_ACTIONS: break

if __name__ == "__main__":
    if not BLOGGER_BLOG_ID or not API_KEYS[0]:
        print("Error: Missing credentials.")
    else:
        main()
