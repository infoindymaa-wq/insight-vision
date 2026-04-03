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
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

# Config
BLOG_ID = os.environ.get("BLOG_ID") or os.getenv("BLOG_ID")
GROQ_API_KEY_1 = os.environ.get("GROQ_API_KEY_1") or os.getenv("GROQ_API_KEY_1")
GROQ_API_KEY_2 = os.environ.get("GROQ_API_KEY_2") or os.getenv("GROQ_API_KEY_2")
API_KEYS = [GROQ_API_KEY_1, GROQ_API_KEY_2]

POSTED_NEWS_FILE = "posted_news.txt"
KEY_INDEX_FILE = "last_key_index.txt"
CAT_INDEX_FILE = "category_counter.txt" # To maintain 10 India : 6 Tech : 54 World ratio
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

# RSS FEEDS
FEEDS = {
    "INDIA": "https://news.google.com/rss/search?q=when:24h+location:india&hl=en-IN&gl=IN&ceid=IN:en",
    "TECH": "https://news.google.com/rss/topics/CAAqKggKIiRDQkFTRlFvSUwyMHZNRGRqTVhZU0JXVnVMVWRDR2dKSlRpZ0FQAQ?hl=en-IN&gl=IN&ceid=IN:en",
    "WORLD": "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
}

def get_rotation_category():
    """Maintains the 10:6:54 ratio logic."""
    count = 0
    if os.path.exists(CAT_INDEX_FILE):
        with open(CAT_INDEX_FILE, "r") as f:
            try: count = int(f.read().strip())
            except: count = 0
    
    # Logic: 
    # 0-9 (10 posts) -> India
    # 10-15 (6 posts) -> Tech
    # 16-69 (54 posts) -> World
    if count < 10: category = "INDIA"
    elif count < 16: category = "TECH"
    else: category = "WORLD"

    next_count = (count + 1) % 70
    with open(CAT_INDEX_FILE, "w") as f:
        f.write(str(next_count))
    return category

def get_current_key():
    index = 0
    if os.path.exists(KEY_INDEX_FILE):
        with open(KEY_INDEX_FILE, "r") as f:
            try: index = int(f.read().strip())
            except: index = 0
    valid_keys = [k for k in API_KEYS if k and len(k) > 5]
    current_key = valid_keys[index % len(valid_keys)]
    with open(KEY_INDEX_FILE, "w") as f:
        f.write(str((index + 1) % len(valid_keys)))
    return current_key

def get_blogger_service():
    creds = None
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else: sys.exit(1)
    return build('blogger', 'v3', credentials=creds)

def get_web_search_image(headline, api_key):
    client = Groq(api_key=api_key)
    try:
        keyword_res = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": f"Give a 3-word English keyword for a news photo: {headline}. Only keyword."}],
            max_tokens=10
        )
        query = keyword_res.choices[0].message.content.strip().strip('"')
        search_url = f"https://www.bing.com/images/search?q={query.replace(' ', '+')}&qft=+filterui:imagesize-large+filterui:aspect-wide&form=IRFLTR&first=1"
        response = requests.get(search_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        soup = BeautifulSoup(response.text, 'html.parser')
        for a in soup.find_all("a", class_="iusc"):
            murl = json.loads(a.get("m", "{}")).get("murl")
            if murl and "google" not in murl and any(ext in murl.lower() for ext in ['.jpg', '.jpeg', '.png']):
                return murl
    except: return None

def generate_ai_article(headline, image_url, category, api_key):
    client = Groq(api_key=api_key)
    image_html = f'<div style="text-align:center;"><img src="{image_url}" style="max-width:100%; border-radius:12px; margin-bottom:25px; box-shadow: 0 4px 15px rgba(0,0,0,0.2);"></div>' if image_url else ""
    
    prompt = f"""
    Headline: {headline}
    Category: {category}
    Task: Write a high-quality, trending news article in English.
    Length: 800 words.
    Style: Professional, engaging, and SEO-optimized.
    Format: Use HTML tags (<h2>, <p>, <b>, <ul>).
    Include: Engaging Intro, In-depth Analysis, and a Conclusion.
    Final Line: Write exactly "Labels: CategoryName, News, Trending" using the actual category.
    """
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": "You are an expert news editor."}, {"role": "user", "content": prompt}],
            max_tokens=3000
        )
        return image_html + completion.choices[0].message.content
    except: return None

def post_to_blogger(service, title, content):
    # Extract labels from the last line of content
    labels = ["News", "Trending"]
    if "Labels:" in content:
        labels_part = content.split("Labels:")[-1].strip().split(",")
        labels = [l.strip() for l in labels_part]
        content = content.split("Labels:")[0].strip()

    body = {"kind": "blogger#post", "title": title, "content": content, "labels": labels}
    try:
        service.posts().insert(blogId=BLOG_ID, body=body).execute()
        return True
    except: return False

def main():
    service = get_blogger_service()
    category = get_rotation_category()
    print(f"DEBUG: Current Category Cycle: {category}")
    
    feed = feedparser.parse(FEEDS[category])
    
    posted_titles = []
    if os.path.exists(POSTED_NEWS_FILE):
        with open(POSTED_NEWS_FILE, "r", encoding="utf-8") as f:
            posted_titles = f.read().splitlines()

    news_to_post = None
    for entry in feed.entries:
        if entry.title not in posted_titles:
            news_to_post = entry
            break

    if not news_to_post:
        print("DEBUG: All news in this category already posted. Switching to fallback.")
        news_to_post = feed.entries[0] # Force first one for testing

    api_key = get_current_key()
    # Generate unique headline
    client = Groq(api_key=api_key)
    head_prompt = f"Rewrite this as a catchy, unique, professional SEO headline: {news_to_post.title}. English only."
    head_res = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": head_prompt}], max_tokens=50)
    unique_headline = head_res.choices[0].message.content.strip().strip('"')

    image_url = get_web_search_image(unique_headline, api_key)
    article_body = generate_ai_article(unique_headline, image_url, category, api_key)
    
    if article_body and post_to_blogger(service, unique_headline, article_body):
        with open(POSTED_NEWS_FILE, "a", encoding="utf-8") as f:
            f.write(news_to_post.title + "\n")
        print(f"SUCCESS: Posted {category} news: {unique_headline}")

if __name__ == "__main__":
    main()
