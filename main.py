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
    """기존 data.json 파일이 존재하면 데이터를 불러옵니다."""
    if os.path.exists(JSON_OUT):
        try:
            with open(JSON_OUT, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"기존 data.json 읽기 에러: {e}")
    return []

def get_bills(page):
    """특정 페이지의 의안 목록을 수집합니다."""
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
    """의안 상세페이지에서 원안 첨부파일을 다운로드합니다."""
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
    """pyhwp 패키지를 활용해 HWP 파일에서 텍스트를 추출합니다."""
    txt_path = file_path + ".txt"
    try:
        cmd = [sys.executable, "-m", "hwp5.hwp5txt", "--output", txt_path, file_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            subprocess.run(["hwp5txt", "--output", txt_path, file_path], check=True, capture_output=True)
            
        with open(txt_path, 'r', encoding='utf-8') as f:
            text = f.read()
        return text
    except Exception as e:
        print(f"   └ [오류] HWP 텍스트 추출 실패: {e}")
        return ""
    finally:
        if os.path.exists(txt_path):
            os.remove(txt_path)

def summarize(text):
    """OpenAI API를 이용하여 텍스트를 요약합니다."""
    if not text or len(text.strip()) < 50: 
        return "본문 텍스트를 읽을 수 없거나 내용이 부족합니다."
    
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
    
    # 1. 기존 저장된 데이터 읽기
    existing_data = load_existing_data()
    existing_urls = {item['detail_url'] for item in existing_data}
    
    print(f"=== 기존 데이터 {len(existing_data)}개 로드 완료. 신규 의안 수집을 시작합니다. ===")

    new_bills_data = []
    page = 1
    stop_crawling = False

    while not stop_crawling:
        print(f"\n[페이지 {page} 탐색 중...]")
        bills = get_bills(page)
        
        if not bills:
            print("더 이상 의안이 없습니다.")
            break
            
        all_bills_in_page_already_exist = True
        
        for i, bill in enumerate(bills):
            # 2. 이미 수집된 의안인지 URL 비교
            if bill['url'] in existing_urls:
                print(f" ({i+1}/{len(bills)}) [기존 의안 - 스킵] {bill['title']}")
                continue
            
            # 3. 신규 의안 감지 시 수집 및 요약 진행
            all_bills_in_page_already_exist = False
            print(f" ({i+1}/{len(bills)}) ✨ [신규 의안 발견] {bill['title']}")
            
            file_path, file_down_url = download_file(bill['url'])
            
            summary = "원안 첨부파일이 없거나 읽을 수 없습니다."
            if file_path:
                if file_path.lower().endswith('.hwp'):
                    text = extract_hwp_text(file_path)
                    if text:
                        summary = summarize(text)
                        print("   └ 요약 완료")
                else:
                    print("   └ HWP 형식이 아닌 파일입니다.")

            new_bills_data.append({
                "title": bill['title'],
                "detail_url": bill['url'],
                "file_url": file_down_url if file_down_url else "",
                "file_name": os.path.basename(file_path) if file_path else "없음",
                "summary": summary
            })

        # 4. 탐색 종료 조건: 한 페이지 전체가 이미 저장된 의안인 경우 이후 페이지 탐색 생략
        if all_bills_in_page_already_exist and len(bills) > 0:
            print(f"\n페이지 {page}의 모든 의안이 이미 수집되어 있습니다. 크롤링을 종료합니다.")
            stop_crawling = True
            break
            
        page += 1

    # 5. 신규 데이터가 존재하는 경우에만 기존 데이터 상단(최신순)에 합쳐서 저장
    if new_bills_data:
        final_data = new_bills_data + existing_data
        with open(JSON_OUT, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        print(f"\n[완료] 신규 의안 {len(new_bills_data)}개가 추가되어 총 {len(final_data)}개 데이터가 저장되었습니다.")
    else:
        print("\n[알림] 새로 제출된 의안이 없습니다. 기존 데이터를 유지합니다.")

if __name__ == '__main__':
    main()
