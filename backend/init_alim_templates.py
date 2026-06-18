"""alim_templates 초기 데이터 삽입"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from backend.database import SessionLocal
from backend import crud

def init_templates():
    db = SessionLocal()
    try:
        # signup 템플릿 추가문구
        crud.upsert_alim_template(
            db,
            template_key="signup",
            extra_text="현재 순위를 미리 확인해두시면 다음 리포트와 비교하실 수 있어요."
        )
        print("[OK] signup 템플릿 추가문구 저장")

        # weekly 템플릿 추가문구
        crud.upsert_alim_template(
            db,
            template_key="weekly",
            extra_text="이번주 놓친 키워드가 있을 수 있어요. 전체 리포트에서 확인해보세요."
        )
        print("[OK] weekly 템플릿 추가문구 저장")

        # 확인
        templates = crud.get_all_alim_templates(db)
        print(f"\n[확인] 저장된 템플릿: {len(templates)}개")
        for t in templates:
            print(f"  - {t.template_key}: {t.extra_text[:30]}...")

    finally:
        db.close()

if __name__ == "__main__":
    init_templates()
