import os
import re
import json
import urllib.parse
import zlib
import olefile
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
    """상세 페이지 접속 후 첨부파일(act=down1) 다운로드"""
    res = requests.get(detail_url, headers=HEADERS)
    soup = BeautifulSoup(res.text, 'html.parser')
    
    down_link = soup.find('a', href=re.compile(r'act=down1'))
    if not down_link:
        # 다운로드 버튼 태그를 찾지 못한 경우 직접 URL 파라미터 구성
        down_url = detail_url.replace('act=view', 'act=down1') + '&judgingNo=1&judgingType=02'
    else:
        href = down_link.get('href')
        down_url = BOARD_URL + href if href.startswith('?') else BASE_URL + href

    try:
        file_res = requests.get(down_url, headers=HEADERS, stream=True)
        if file_res.status_code != 200 or len(file_res.content) < 500:
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
        print(f"   └ [다운로드 실패]: {e}")
        return None, None

def extract_hwp_text(file_path):
    """olefile과 zlib를 사용하여 파이썬 내부에서 HWP 본문 텍스트를 직접 추출합니다."""
    if not olefile.isOleFile(file_path):
        return ""

    text = ""
    try:
        ole = olefile.OleFileIO(file_path)
        dirs = ole.listdir()
        
        # HWP 문서의 본문 텍스트가 담긴 BodyText 섹션 탐색
        sections = [d for d in dirs if d[0] == 'BodyText']
        
        for section in sections:
            stream = ole.openstream(section)
            data = stream.read()
            
            # HWP 5.0 압축 해제
            try:
                decompressed = zlib.decompress(data, -15)
            except Exception:
                try:
                    decompressed = zlib.decompress(data)
                except Exception:
                    decompressed = data
            
            # UTF-16LE 텍스트 복원 및 한글/영문 추출
            decoded = decompressed.decode('utf-16le', errors='ignore')
            clean_text = re.sub(r'[^가-힣0-9a-zA-Z\s.,\-\(\)]', ' ', decoded)
            text += ' '.join(clean_text.split()) + "\n"
            
        ole.close()
        return text
    except Exception as e:
        print(f"   └ [HWP 읽기 실패]: {e}")
        return ""

def summarize(text):
    """OpenAI API를 사용해 의안을 요약합니다."""
    if not text or len(text.strip()) < 30: 
        return "본문 텍스트를 읽을 수 없어 요약에 실패했습니다."
    
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system", 
                    "content": "너는 지방의회 의안 분석 전문가야. 주어진 텍스트에서 '제안 이유'와 '주요 내용'을 파악하여 핵심을 3개 내외의 글머리 기호(Bullet points)로 간결하게 요약해줘."
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
    
    print(f"=== 제주도의회 수집 시작 (기존 수집건: {len(existing_data)}개) ===")

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
                print(f" ({i+1}/{len(bills)}) [기존 건 스킵] {bill['title']}")
                continue
            
            all_bills_in_page_already_exist = False
            print(f" ({i+1}/{len(bills)}) ⚙️ [수집 및 요약 중] {bill['title']}")
            
            file_path, file_down_url = download_file(bill['url'])
            
            if file_path:
                text = extract_hwp_text(file_path)
                if text:
                    summary = summarize(text)
                    print("   └ ✅ 요약 성공")
                else:
                    summary = "HWP 파일 내 텍스트 추출에 실패했습니다."
                    print("   └ ❌ 텍스트 추출 실패")
            else:
                summary = "본 의안은 원안 첨부파일이 제공되지 않는 의안입니다."
                print("   └ ⚪ 첨부파일 없음")

            new_bills_data.append({
                "title": bill['title'],
                "detail_url": bill['url'],
                "file_url": file_down_url if file_down_url else "",
                "file_name": os.path.basename(file_path) if file_path else "없음",
                "summary": summary
            })

        if all_bills_in_page_already_exist and len(bills) > 0:
            print(f"\n페이지 {page}의 모든 데이터가 이미 수집되어 종료합니다.")
            stop_crawling = True
            break
            
        page += 1

    if new_bills_data:
        final_data = new_bills_data + existing_data
        with open(JSON_OUT, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        print(f"\n[완료] 총 {len(final_data)}개 저장 완료.")
    else:
        print("\n[알림] 추가된 신규 의안이 없습니다.")

if __name__ == '__main__':
    main()
