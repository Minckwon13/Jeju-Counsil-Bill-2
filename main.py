import os
import re
import sys
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

def setup():
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)

def load_existing_data():
    if os.path.exists(JSON_OUT):
        try:
            with open(JSON_OUT, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []

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
    """유연한 다운로드 링크 수집 및 주소 직접 구성 백업 함수"""
    res = requests.get(detail_url, headers=HEADERS)
    soup = BeautifulSoup(res.text, 'html.parser')
    
    down_url = None
    # 1. 태그 내 다운로드 관련 링크 탐색
    for a in soup.find_all('a'):
        href = a.get('href', '')
        onclick = a.get('onclick', '')
        text = a.text.strip()
        
        if 'down' in href.lower() or 'down' in onclick.lower() or '원안' in text or '첨부' in text:
            if href and not href.startswith('javascript'):
                down_url = BOARD_URL + href if href.startswith('?') else (href if href.startswith('http') else BASE_URL + href)
                break
                
    # 2. 링크를 찾지 못한 경우 URL 파라미터 기반 자동 생성 (act=view -> act=down1)
    if not down_url:
        down_url = detail_url.replace('act=view', 'act=down1') + '&judgingNo=1&judgingType=02'

    try:
        file_res = requests.get(down_url, headers=HEADERS, stream=True)
        if file_res.status_code != 200 or len(file_res.content) < 1000:
            return None, None

        filename = "bill_file.hwp"
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
    except Exception as e:
        print(f"   └ [다운로드 에러]: {e}")
        return None, None

def extract_hwp_text(file_path):
    """hwp5txt 실행 및 바이너리 직접 분석을 포함한 이중 텍스트 추출"""
    txt_path = file_path + ".txt"
    text = ""
    
    # 1차: pyhwp / hwp5txt 도구 활용
    try:
        cmd = [sys.executable, "-m", "hwp5.hwp5txt", "--output", txt_path, file_path]
        subprocess.run(cmd, check=True, capture_output=True)
        with open(txt_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except Exception:
        try:
            subprocess.run(["hwp5txt", "--output", txt_path, file_path], check=True, capture_output=True)
            with open(txt_path, 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception:
            pass
    finally:
        if os.path.exists(txt_path):
            os.remove(txt_path)
            
    # 2차 백업: 직접 인코딩 해제 (hwp5txt 실패 시)
    if not text.strip():
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
                decoded = content.decode('utf-16le', errors='ignore')
                hangul_only = re.sub(r'[^가-힣0-9a-zA-Z\s.,]', ' ', decoded)
                text = ' '.join(hangul_only.split())
        except Exception:
            pass

    return text

def summarize(text):
    if not text or len(text.strip()) < 30: 
        return "본문 텍스트를 파싱하지 못해 요약할 수 없습니다."
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system", 
                    "content": "너는 지방의회 의안 분석 전문가야. 주어진 텍스트에서 '제안 이유'와 '주요 내용'을 파악하여 핵심을 3개 내외의 글머리 기호(Bullet points)로 요약해줘."
                },
                {"role": "user", "content": text[:3000]}
            ]
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"요약 중 API 오류 발생: {e}"

def main():
    setup()
    existing_data = load_existing_data()
    existing_urls = {item['detail_url'] for item in existing_data}
    
    print(f"=== 기존 데이터 {len(existing_data)}개 로드 완료. 수집을 시작합니다. ===")

    new_bills_data = []
    page = 1
    stop_crawling = False

    while not stop_crawling:
        print(f"\n[페이지 {page} 탐색 중...]")
        bills = get_bills(page)
        
        if not bills:
            break
            
        all_bills_in_page_already_exist = True
        
        for i, bill in enumerate(bills):
            if bill['url'] in existing_urls:
                print(f" ({i+1}/{len(bills)}) [기존 의안 - 스킵] {bill['title']}")
                continue
            
            all_bills_in_page_already_exist = False
            print(f" ({i+1}/{len(bills)}) ✨ [수집 진행] {bill['title']}")
            
            file_path, file_down_url = download_file(bill['url'])
            summary = "원안 첨부파일을 내려받지 못했습니다."
            
            if file_path:
                text = extract_hwp_text(file_path)
                if text:
                    summary = summarize(text)
                    print("   └ 요약 성공")
                else:
                    summary = "HWP 파일 텍스트 추출 실패"
                    print("   └ 텍스트 추출 실패")

            new_bills_data.append({
                "title": bill['title'],
                "detail_url": bill['url'],
                "file_url": file_down_url if file_down_url else "",
                "file_name": os.path.basename(file_path) if file_path else "없음",
                "summary": summary
            })

        if all_bills_in_page_already_exist and len(bills) > 0:
            print(f"\n페이지 {page}의 모든 의안이 이미 수집되어 크롤링을 종료합니다.")
            stop_crawling = True
            break
            
        page += 1

    if new_bills_data:
        final_data = new_bills_data + existing_data
        with open(JSON_OUT, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        print(f"\n[완료] 총 {len(final_data)}개 데이터 저장 완료.")
    else:
        print("\n[알림] 추가할 신규 의안이 없습니다.")

if __name__ == '__main__':
    main()
