# 🍊 제주특별자치도의회 13대 의안 자동 요약 대시보드

제주특별자치도의회 13대 의안 페이지에서 원안(HWP) 파일을 크롤링하여 OpenAI API로 자동 요약하고, 이를 GitHub Pages 웹 대시보드로 시각화하는 파이썬 프로젝트입니다.

---

## 📁 프로젝트 구조

```text
.
├── main.py        # 의안 크롤링, HWP 텍스트 추출 및 OpenAI 요약 후 data.json 생성
├── index.html     # GitHub Pages에서 data.json을 불러와 보여주는 웹 대시보드
├── data.json      # 수집 및 요약 결과 데이터 (main.py 실행 시 자동 생성/갱신)
└── README.md      # 프로젝트 설명 문서
