import os
import time
import pickle
import feedparser
import requests
import random
import json
import sys
import re
from bs4 import BeautifulSoup
from groq import Groq
from datetime import datetime, timedelta, timezone
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()

# Config
BLOG_ID = os.environ.get("BLOG_ID") or os.getenv("BLOG_ID")
API_KEYS = [os.environ.get("GROQ_API_KEY_1"), os.environ.get("GROQ_API_KEY_2")]

POSTED_NEWS_FILE = "posted_news.txt"
KEY_INDEX_FILE = "last_key_index.txt"
CAT_INDEX_FILE = "category_counter.txt" 
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS") == "true"

FEEDS = {
    "INDIA": "https://news.google.com/rss/search?q=when:24h+location:india&hl=en-IN&gl=IN&ceid=IN:en",
    "TECH": "https://news.google.com/rss/topics/CAAqKggKIiRDQkFTRlFvSUwyMHZNRGRqTVhZU0JXVnVMVWRDR2dKSlRpZ0FQAQ?hl=en-IN&gl=IN&ceid=IN:en",
    "BUSINESS": "https://news.google.com/rss/topics/CAAqKggKIiRDQkFTRlFvSUwyMHZNRGx6TVdZd0pXVnVMVWRDR2dKSlRpZ0FQAQ?hl=en-IN&gl=IN&ceid=IN:en",
    "ECONOMY": "https://news.google.com/rss/search?q=economy+when:24h&hl=en-IN&gl=IN&ceid=IN:en",
    "SCIENCE": "https://news.google.com/rss/topics/CAAqKggKIiRDQkFTRlFvSUwyMHZNRFp0Y1RjU0JXVnVMVWRDR2dKSlRpZ0FQAQ?hl=en-IN&gl=IN&ceid=IN:en",
    "EDUCATION": "https://news.google.com/rss/search?q=education+when:24h&hl=en-IN&gl=IN&ceid=IN:en",
    "WORLD": "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
}

CAT_ORDER = ["WORLD", "INDIA", "TECH", "WORLD", "BUSINESS", "ECONOMY", "WORLD", "SCIENCE", "EDUCATION", "INDIA"]

def get_rotation_category():
    count = 0
    if os.path.exists(CAT_INDEX_FILE):
        with open(CAT_INDEX_FILE, "r") as f:
            try: count = int(f.read().strip())
            except: count = 0
    category = CAT_ORDER[count % len(CAT_ORDER)]
    with open(CAT_INDEX_FILE, "w") as f:
        f.write(str((count + 1) % 70))
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
    
    # Advanced Image HTML with Alt Text
    alt_text = f"{headline} - Latest News Update"
    image_html = f'<div style="text-align:center;"><img src="{image_url}" alt="{alt_text}" title="{alt_text}" style="max-width:100%; border-radius:12px; margin-bottom:25px; box-shadow: 0 4px 15px rgba(0,0,0,0.2);"></div>' if image_url else ""
    
    prompt = f"""
    Headline: {headline}
    Category: {category}
    Task: Write an SEO-optimized professional news report in English.
    
    Structure Requirements:
    1. Start with a "Search Description:" line (exactly 150 chars summary with keywords).
    2. Start the news report directly.
    3. Use <h2> and <h3> tags for sub-sections. Use "Title: [Section Name]" inside <h2>.
    4. Include an "Internal Link Suggestion" section at the end.
    
    Guidelines:
    - Neutral, journalistic tone (Reuters style).
    - No AI-generated phrases or flowery intros.
    - Length: 800 words.
    - Final Line must be: Labels: {category}, News, Trending
    """
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": "You are an SEO expert and senior news editor. You write high-ranking, factual news reports."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            max_tokens=3500
        )
        content = completion.choices[0].message.content
        
        # Internal linking addition
        internal_link = f'<p><br><b>Read more trending stories on our <a href="/">homepage</a>.</b></p>'
        
        return image_html + content + internal_link
    except: return None

def post_to_blogger(service, title, content):
    labels = ["News"]
    if "Labels:" in content:
        labels_part = content.split("Labels:")[-1].strip().split(",")
        labels = [l.strip() for l in labels_part]
        content = content.split("Labels:")[0].strip()

    # Clean up the Search Description from the body if possible (Blogger theme will handle it)
    body = {"kind": "blogger#post", "title": title, "content": content, "labels": labels}
    try:
        service.posts().insert(blogId=BLOG_ID, body=body).execute()
        return True
    except: return False

def main():
    service = get_blogger_service()
    category = get_rotation_category()
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
        feed = feedparser.parse(FEEDS["WORLD"])
        for entry in feed.entries:
            if entry.title not in posted_titles:
                news_to_post = entry
                break

    if not news_to_post: return

    api_key = get_current_key()
    client = Groq(api_key=api_key)
    head_prompt = f"Convert this into a professional, SEO-rich journalistic headline: {news_to_post.title}. Return only the title."
    head_res = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=[{"role": "user", "content": head_prompt}], max_tokens=50)
    unique_headline = head_res.choices[0].message.content.strip().strip('"')

    image_url = get_web_search_image(unique_headline, api_key)
    article_body = generate_ai_article(unique_headline, image_url, category, api_key)
    
    if article_body and post_to_blogger(service, unique_headline, article_body):
        # DOUBLE CHECK before recording
        fresh_titles = []
        if os.path.exists(POSTED_NEWS_FILE):
            with open(POSTED_NEWS_FILE, "r", encoding="utf-8") as f:
                fresh_titles = f.read().splitlines()
        
        if news_to_post.title not in fresh_titles:
            with open(POSTED_NEWS_FILE, "a", encoding="utf-8") as f:
                f.write(news_to_post.title + "\n")
            print(f"SUCCESS: Posted SEO-optimized {category} news: {unique_headline}")

if __name__ == "__main__":
    main()
