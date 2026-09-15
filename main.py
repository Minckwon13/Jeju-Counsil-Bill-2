import os
import re
import json
import urllib.parse
import subprocess
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

# OpenAI 클라이언트 초기화
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

BASE_URL = "https://www.council.jeju.kr"
BOARD_URL = f"{BASE_URL}/activity/bill/info/13dae.do"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
SAVE_DIR = "./jeju_bills"
JSON_OUT = "./data.json"
MAX_PAGES = 2  # 크롤링할 페이지 수

def setup():
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)

def get_bills(page):
    url = f"{BOARD_URL}?page={page}"
    res = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(res.text, 'html.parser')
    links = soup.find_all('a', href=re.compile(r'act=view'))
    
    bill_list = []
    for link in links:
        href = link.get('href')
        title = link.text.strip()
        full_url = BOARD_URL + href if href.startswith('?') else BASE_URL + href
        if title and full_url not in [b['url'] for b in bill_list]:
            bill_list.append({'title': title, 'url': full_url})
    return bill_list

def download_file(detail_url):
    res = requests.get(detail_url, headers=HEADERS)
    soup = BeautifulSoup(res.text, 'html.parser')
    down_link = soup.find('a', href=re.compile(r'act=down1'))
    if not down_link: 
        return None, None
        
    href = down_link.get('href')
    down_url = BOARD_URL + href if href.startswith('?') else BASE_URL + href
    file_res = requests.get(down_url, headers=HEADERS, stream=True)
    
    filename = "unknown_file.hwp"
    if "Content-Disposition" in file_res.headers:
        content_disp = file_res.headers["Content-Disposition"]
        filenames = re.findall(r'filename="?([^"]+)"?', content_disp)
        if filenames:
            filename = urllib.parse.unquote(filenames[0].encode('latin1').decode('utf8', 'ignore'))
            
    filename = re.sub(r'[\\/*?:"<>|]', "", filename)
    save_path = os.path.join(SAVE_DIR, filename)
    
    with open(save_path, 'wb') as f:
        for chunk in file_res.iter_content(chunk_size=8192):
            f.write(chunk)
            
    return save_path, down_url

def extract_hwp_text(file_path):
    txt_path = file_path + ".txt"
    try:
        subprocess.run(["hwp5txt", "--output", txt_path, file_path], check=True, capture_output=True)
        with open(txt_path, 'r', encoding='utf-8') as f:
            text = f.read()
        return text
    except Exception as e:
        print(f"HWP 텍스트 추출 실패: {e}")
        return ""
    finally:
        if os.path.exists(txt_path):
            os.remove(txt_path)

def summarize(text):
    if not text or len(text.strip()) < 50: 
        return "본문 내용이 부족하여 요약을 제공할 수 없습니다."
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system", 
                    "content": "너는 지방의회 의안 분석 전문가야. 주어진 텍스트에서 '제안 이유'와 '주요 내용'을 요약해줘. 핵심 내용 3글머리 기호(Bullet Points)로 간결하게 작성해."
                },
                {"role": "user", "content": text[:3000]}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"요약 중 오류 발생: {e}"

def main():
    setup()
    results = []

    for page in range(1, MAX_PAGES + 1):
        print(f"=== {page} 페이지 크롤링 중 ===")
        bills = get_bills(page)
        
        for i, bill in enumerate(bills):
            print(f"[{i+1}/{len(bills)}] {bill['title']}")
            file_path, file_down_url = download_file(bill['url'])
            
            summary = "원안 첨부파일이 없어 요약되지 않았습니다."
            if file_path and file_path.lower().endswith('.hwp'):
                text = extract_hwp_text(file_path)
                if text:
                    summary = summarize(text)

            results.append({
                "title": bill['title'],
                "detail_url": bill['url'],
                "file_url": file_down_url if file_down_url else "",
                "file_name": os.path.basename(file_path) if file_path else "없음",
                "summary": summary
            })

    # JSON 데이터 저장
    with open(JSON_OUT, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n성공적으로 데이터 저장이 완료되었습니다: {JSON_OUT}")

if __name__ == '__main__':
    main()
