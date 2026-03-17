#!/usr/bin/env python3
"""
Generate candidate queries for the gold standard evaluation dataset.

Strategy: Work backwards from documents to queries. Each topic cluster in the
corpus yields 2-5 queries at different difficulty levels.

Output: gold_standard.json with 50+ queries, pre-populated relevance judgments,
and candidate contradictory pairs.
"""

import json
from datetime import date
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent.parent
CORPUS_MANIFEST = EVAL_DIR / "corpus_manifest.json"
OUTPUT = EVAL_DIR / "gold_standard.json"


def load_manifest():
    with open(CORPUS_MANIFEST, "r", encoding="utf-8") as f:
        return json.load(f)


def doc_ids_by_pattern(manifest, **kwargs):
    """Find doc_ids matching given field patterns."""
    results = []
    for doc in manifest:
        if doc.get("excluded"):
            continue
        match = True
        for field, pattern in kwargs.items():
            val = doc.get(field, "")
            if isinstance(pattern, list):
                if not any(p.lower() in val.lower() for p in pattern):
                    match = False
            elif pattern.lower() not in val.lower():
                match = False
        if match:
            results.append(doc["doc_id"])
    return results


def build_queries(manifest):
    """Build all candidate queries."""
    queries = []
    qnum = 0

    def next_id():
        nonlocal qnum
        qnum += 1
        return f"Q{qnum:03d}"

    def make_judgments(relevant_ids, related_ids=None, hard_neg_ids=None):
        """Build relevance_judgments array."""
        j = []
        for did in relevant_ids:
            j.append({"doc_id": did, "relevance": 3, "rationale": "directly on point"})
        for did in (related_ids or []):
            j.append({"doc_id": did, "relevance": 2, "rationale": "related topic/court"})
        for did in (hard_neg_ids or []):
            j.append({"doc_id": did, "relevance": 0, "rationale": "same branch, different topic"})
        return j

    # Helper: get some hard negatives from same branch but different topic
    hukuk_docs = doc_ids_by_pattern(manifest, law_branch="hukuk")
    ceza_docs = doc_ids_by_pattern(manifest, law_branch="ceza")
    idari_docs = doc_ids_by_pattern(manifest, law_branch="idari")

    # =====================================================================
    # CLUSTER 1: Muris Muvazaası / Tapu İptali (1. HD, HGK, İBK)
    # =====================================================================
    hd1_muvazaa = doc_ids_by_pattern(manifest, daire="1. Hukuk Dairesi")
    hgk_muvazaa = [d for d in doc_ids_by_pattern(manifest, daire="Hukuk Genel Kurulu")
                   if any(k in d.lower() for k in ["2015-1895", "2015_983", "2012_1141"])]
    ibk_muvazaa = doc_ids_by_pattern(manifest, daire=["İçtihatları Birleştirme HGK"])

    muvazaa_all = hd1_muvazaa + hgk_muvazaa + ibk_muvazaa
    muvazaa_neg = [d for d in ceza_docs[:3]]

    queries.append({
        "query_id": next_id(),
        "query_text": "Muris muvazaası nedeniyle tapu iptali ve tescil davası",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 1. HD", "HGK"],
        "relevance_judgments": make_judgments(hd1_muvazaa[:3], hgk_muvazaa + ibk_muvazaa, muvazaa_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Miras bırakanın sağlığında mirasçılardan mal kaçırmak amacıyla yaptığı satış işleminin geçersizliği",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 1. HD", "HGK"],
        "relevance_judgments": make_judgments(hd1_muvazaa[:2] + ibk_muvazaa, hgk_muvazaa, muvazaa_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Babam vefat etmeden önce evi kardeşime sattı ama gerçek bir satış değildi, ne yapabilirim?",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 1. HD"],
        "relevance_judgments": make_judgments(hd1_muvazaa[:2], ibk_muvazaa + hgk_muvazaa[:1], muvazaa_neg),
        "difficulty": "hard"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "1974 tarihli muris muvazaası içtihadı birleştirme kararı",
        "query_type": "case_law_search",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay İBK"],
        "relevance_judgments": make_judgments(ibk_muvazaa, hd1_muvazaa[:2], muvazaa_neg),
        "difficulty": "easy"
    })

    # =====================================================================
    # CLUSTER 2: Boşanma / Aile Hukuku (2. HD)
    # =====================================================================
    hd2 = doc_ids_by_pattern(manifest, daire="2. Hukuk Dairesi")
    bosanma_neg = [d for d in idari_docs[:3]]

    queries.append({
        "query_id": next_id(),
        "query_text": "Anlaşmalı boşanma davasında tarafların eşit kusurlu kabul edilmesi",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 2. HD"],
        "relevance_judgments": make_judgments(hd2[:3], [], bosanma_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Boşanma davasında yoksulluk nafakası ve manevi tazminat talepleri",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 2. HD"],
        "relevance_judgments": make_judgments(hd2[:2], hd2[2:4], bosanma_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Eşlerin evlilik birliğinde karşılıklı kusurlu davranışları ve tazminat hakları",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 2. HD"],
        "relevance_judgments": make_judgments(hd2[:2], hd2[2:5], bosanma_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Velayet hakkının değiştirilmesi ve çocuğun üstün yararı ilkesi",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 2. HD"],
        "relevance_judgments": make_judgments([], hd2[:3], bosanma_neg),
        "difficulty": "medium"
    })

    # =====================================================================
    # CLUSTER 3: İş Hukuku — Kıdem/İhbar Tazminatı (9. HD, 22. HD, HGK)
    # =====================================================================
    hd9 = doc_ids_by_pattern(manifest, daire="9. Hukuk Dairesi")
    hd22 = doc_ids_by_pattern(manifest, daire="22. Hukuk Dairesi")
    hgk_all = doc_ids_by_pattern(manifest, daire="Hukuk Genel Kurulu")
    is_neg = [d for d in ceza_docs[:3]]

    queries.append({
        "query_id": next_id(),
        "query_text": "İş sözleşmesinin feshinde kıdem ve ihbar tazminatı hesaplaması",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 9. HD", "Yargıtay 22. HD"],
        "relevance_judgments": make_judgments(hd9[:2], hd22[:2], is_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "İşe iade davasında işverenin fesih gerekçesinin geçerli neden oluşturup oluşturmadığı",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 9. HD", "Yargıtay 22. HD"],
        "relevance_judgments": make_judgments(hd9[:2] + hd22[:2], hgk_all[:1], is_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Toplu iş sözleşmesinden kaynaklanan işçilik alacaklarında zamanaşımı süresi",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 9. HD", "HGK"],
        "relevance_judgments": make_judgments(hgk_all[:2], hd9[:2] + hd22[:1], is_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Patronum beni sebepsiz yere çıkardı, 8 yıllık çalışanım, haklarım nedir?",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 9. HD"],
        "relevance_judgments": make_judgments(hd9[:2], hd22[:2] + hgk_all[:1], is_neg),
        "difficulty": "hard"
    })

    # =====================================================================
    # CLUSTER 4: İş Kazası / Sosyal Güvenlik (21. HD)
    # =====================================================================
    hd21 = doc_ids_by_pattern(manifest, daire="21. Hukuk Dairesi")
    hgk_is_kazasi = [d for d in hgk_all if "2015_983" in d or "2015-983" in d]

    queries.append({
        "query_id": next_id(),
        "query_text": "İş kazası nedeniyle maddi ve manevi tazminat davası",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 21. HD", "HGK"],
        "relevance_judgments": make_judgments(hd21, hgk_is_kazasi, is_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "İş kazasında işverenin kusur oranının belirlenmesi ve bilirkişi raporu",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 21. HD"],
        "relevance_judgments": make_judgments(hd21[:2], hgk_is_kazasi, is_neg),
        "difficulty": "medium"
    })

    # =====================================================================
    # CLUSTER 5: Belirsiz Alacak Davası (YİBBGK, 9. HD, 22. HD)
    # =====================================================================
    yibbgk = doc_ids_by_pattern(manifest, daire=["İçtihatları Birleştirme BGK"])

    queries.append({
        "query_id": next_id(),
        "query_text": "Belirsiz alacak davası olarak açılan işçilik alacağı talebi",
        "query_type": "contradictory_precedent",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay YİBBGK", "Yargıtay 9. HD", "Yargıtay 22. HD"],
        "relevance_judgments": make_judgments(yibbgk + hd9[:2], hd22[:2], is_neg),
        "contradictory_pairs": [{
            "doc_a": hd9[0] if hd9 else "",
            "doc_b": hd22[0] if hd22 else "",
            "description": "9. HD ve 22. HD arasında işçilik alacaklarının belirsiz alacak olarak talep edilip edilemeyeceği konusundaki görüş ayrılığı"
        }],
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "HMK m.107 kapsamında belirsiz alacak davasının şartları ve işçilik alacaklarında uygulanması",
        "query_type": "statute_application",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay YİBBGK", "Yargıtay 9. HD"],
        "relevance_judgments": make_judgments(yibbgk, hd9[:2] + hd22[:2], is_neg),
        "difficulty": "easy"
    })

    # =====================================================================
    # CLUSTER 6: Kasten Öldürme / Ceza (1. CD)
    # =====================================================================
    cd1 = doc_ids_by_pattern(manifest, daire="1. Ceza Dairesi")
    ceza_neg = [d for d in hukuk_docs[:3]]

    queries.append({
        "query_id": next_id(),
        "query_text": "Kasten öldürme suçunda haksız tahrik indirimi uygulaması",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 1. CD"],
        "relevance_judgments": make_judgments(cd1[:3], cd1[3:5], ceza_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "TCK m.81 kasten öldürme suçunun nitelikli halleri ve ceza tayini",
        "query_type": "statute_application",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 1. CD"],
        "relevance_judgments": make_judgments(cd1[:4], cd1[4:6], ceza_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Adam öldürmeye teşebbüs suçunda meşru müdafaa savunması",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 1. CD"],
        "relevance_judgments": make_judgments(cd1[:2], cd1[2:5], ceza_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Neticesi sebebiyle ağırlaşmış yaralama suçu ile kasten öldürmeye teşebbüs ayrımı",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 1. CD"],
        "relevance_judgments": make_judgments(cd1[:3], cd1[3:6], ceza_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Kavga sırasında bıçakla yaralama sonucu ölüm meydana gelmesi halinde suç vasfı",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 1. CD"],
        "relevance_judgments": make_judgments(cd1[:2], cd1[2:5], ceza_neg),
        "difficulty": "hard"
    })

    # =====================================================================
    # CLUSTER 7: Uyuşturucu Suçları (10. CD)
    # =====================================================================
    cd10 = doc_ids_by_pattern(manifest, daire="10. Ceza Dairesi")

    queries.append({
        "query_id": next_id(),
        "query_text": "Uyuşturucu madde ticareti yapma suçunda etkin pişmanlık hükmü",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 10. CD"],
        "relevance_judgments": make_judgments(cd10, [], ceza_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Kullanmak amacıyla uyuşturucu madde bulundurma ile ticaret ayrımı kriterleri",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 10. CD"],
        "relevance_judgments": make_judgments(cd10[:2], cd10[2:], ceza_neg),
        "difficulty": "medium"
    })

    # =====================================================================
    # CLUSTER 8: Terör Örgütü Üyeliği (3. CD)
    # =====================================================================
    cd3 = doc_ids_by_pattern(manifest, daire="3. Ceza Dairesi")

    queries.append({
        "query_id": next_id(),
        "query_text": "Silahlı terör örgütüne üye olma suçunda ByLock kullanımının delil değeri",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 3. CD"],
        "relevance_judgments": make_judgments(cd3, [], ceza_neg),
        "difficulty": "easy"
    })

    # =====================================================================
    # CLUSTER 9: Vergi — İhtiyati Haciz / Tarhiyat (Danıştay, VDDK, İBK)
    # =====================================================================
    dan3 = doc_ids_by_pattern(manifest, daire="3. Daire")
    dan7 = doc_ids_by_pattern(manifest, daire="7. Daire")
    vddk = doc_ids_by_pattern(manifest, daire="Vergi Dava Daireleri Kurulu")
    dan_ibk = doc_ids_by_pattern(manifest, daire="İçtihatları Birleştirme Kurulu")
    bim = doc_ids_by_pattern(manifest, court="BİM")
    idari_neg = [d for d in hukuk_docs[:3]]

    queries.append({
        "query_id": next_id(),
        "query_text": "Vergi incelemesi sonrası tarhiyat yapıldıktan sonra ihtiyati haciz uygulanabilir mi?",
        "query_type": "contradictory_precedent",
        "law_branch": "idari",
        "relevant_court": ["Danıştay İBK", "Danıştay 3. D.", "VDDK"],
        "relevance_judgments": make_judgments(dan_ibk + vddk[:2], dan3, idari_neg),
        "contradictory_pairs": [{
            "doc_a": dan3[0] if dan3 else "",
            "doc_b": vddk[0] if vddk else "",
            "description": "Danıştay 3. ve 9. Daire ile 4. ve 7. Daire arasında tarhiyat sonrası ihtiyati haciz konusundaki görüş ayrılığı — İBK 2021/6 ile çözülmüştür"
        }],
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "6183 sayılı Kanun m.13 uyarınca ihtiyati haciz işleminin şartları",
        "query_type": "statute_application",
        "law_branch": "idari",
        "relevant_court": ["Danıştay İBK", "VDDK"],
        "relevance_judgments": make_judgments(dan_ibk, vddk[:2] + dan3[:1], idari_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Vergi dairesinin mükellefi hakkında tesis ettiği ödeme emrinin iptali",
        "query_type": "topical",
        "law_branch": "idari",
        "relevant_court": ["VDDK", "BİM"],
        "relevance_judgments": make_judgments(vddk[:2] + bim[:1], dan3[:1], idari_neg),
        "difficulty": "medium"
    })

    # =====================================================================
    # CLUSTER 10: Vergi — KDV İadesi (VDDK, BİM)
    # =====================================================================
    queries.append({
        "query_id": next_id(),
        "query_text": "İndirimli orana tabi KDV iade alacağının hesaplanması ve mahsubu",
        "query_type": "topical",
        "law_branch": "idari",
        "relevant_court": ["VDDK", "BİM"],
        "relevance_judgments": make_judgments(bim, vddk[:2], idari_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Konut teslimlerinde KDV oranı ve büro niteliğindeki taşınmazların durumu",
        "query_type": "topical",
        "law_branch": "idari",
        "relevant_court": ["VDDK", "BİM"],
        "relevance_judgments": make_judgments(bim[:1], vddk[:2], idari_neg),
        "contradictory_pairs": [{
            "doc_a": bim[0] if bim else "",
            "doc_b": bim[1] if len(bim) > 1 else "",
            "description": "Büro olarak nitelendirilen ancak fiilen konut olarak kullanılan taşınmazlarda KDV oranı uyuşmazlığı"
        }] if len(bim) > 1 else [],
        "difficulty": "hard"
    })

    # =====================================================================
    # CLUSTER 11: Danıştay 8. Daire — İdari İşlem İptali
    # =====================================================================
    dan8 = doc_ids_by_pattern(manifest, daire="8. Daire")
    iddk = doc_ids_by_pattern(manifest, daire="İdari Dava Daireleri Kurulu")

    queries.append({
        "query_id": next_id(),
        "query_text": "İdari işlemin iptali davasında yürütmenin durdurulması kararı",
        "query_type": "topical",
        "law_branch": "idari",
        "relevant_court": ["Danıştay 8. D."],
        "relevance_judgments": make_judgments(dan8[:3], iddk, idari_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Kamu görevlisinin disiplin cezasına karşı idari yargıda dava açma süresi",
        "query_type": "procedural",
        "law_branch": "idari",
        "relevant_court": ["Danıştay 8. D.", "İdare Mahkemesi"],
        "relevance_judgments": make_judgments(dan8[:2], iddk[:1], idari_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Danıştay İdari Dava Daireleri Kurulu ısrar kararlarına ilişkin emsal",
        "query_type": "case_law_search",
        "law_branch": "idari",
        "relevant_court": ["İDDK"],
        "relevance_judgments": make_judgments(iddk, dan8[:2], idari_neg),
        "difficulty": "easy"
    })

    # =====================================================================
    # CLUSTER 12: Kamulaştırma (5. HD)
    # =====================================================================
    hd5 = doc_ids_by_pattern(manifest, daire=["5. Hukuk Dairesi", "5. HD"])

    queries.append({
        "query_id": next_id(),
        "query_text": "Kamulaştırma bedelinin tespiti davasında taşınmazın değerinin belirlenmesi",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 5. HD"],
        "relevance_judgments": make_judgments(hd5, [], is_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Acele kamulaştırma kararının hukuka uygunluğu ve denetimi",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 5. HD", "Danıştay 8. D.", "İDDK"],
        "relevance_judgments": make_judgments(hd5[:1], dan8[:2] + iddk[:1], idari_neg),
        "difficulty": "medium"
    })

    # =====================================================================
    # CLUSTER 13: BAM Kararları
    # =====================================================================
    bam_hd = [d for d in doc_ids_by_pattern(manifest, court="BAM") if "hd" in d.lower() or "Hukuk" in d]
    bam_cd = [d for d in doc_ids_by_pattern(manifest, court="BAM") if "cd" in d.lower() or "Ceza" in d]

    queries.append({
        "query_id": next_id(),
        "query_text": "Bölge Adliye Mahkemesi istinaf incelemesinde hukuki denetim kapsamı",
        "query_type": "procedural",
        "law_branch": "hukuk",
        "relevant_court": ["BAM"],
        "relevance_judgments": make_judgments(bam_hd, bam_cd, idari_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Menfi tespit davasında ispat yükü ve BAM kararları",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["BAM"],
        "relevance_judgments": make_judgments(bam_hd[:2], [], is_neg),
        "difficulty": "medium"
    })

    # =====================================================================
    # CLUSTER 14: İlk Derece — Ticaret Mahkemeleri
    # =====================================================================
    ticaret = [d for d in doc_ids_by_pattern(manifest, court="İlk Derece")
               if "ticaret" in d.lower() or "Ticaret" in d]

    queries.append({
        "query_id": next_id(),
        "query_text": "Eser sözleşmesinden kaynaklanan alacak davası ve yüklenicinin sorumluluğu",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Asliye Ticaret Mahkemesi"],
        "relevance_judgments": make_judgments(ticaret[:2], ticaret[2:], is_neg),
        "difficulty": "medium"
    })

    # =====================================================================
    # CLUSTER 15: AYM Bireysel Başvuru
    # =====================================================================
    aym = doc_ids_by_pattern(manifest, court="AYM")

    queries.append({
        "query_id": next_id(),
        "query_text": "Anayasa Mahkemesi bireysel başvuruda adil yargılanma hakkı ihlali",
        "query_type": "topical",
        "law_branch": "anayasa",
        "relevant_court": ["AYM"],
        "relevance_judgments": make_judgments(aym, [], ceza_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Grev hakkının engellenmesi nedeniyle AYM'ye bireysel başvuru",
        "query_type": "contradictory_precedent",
        "law_branch": "anayasa",
        "relevant_court": ["AYM", "Yargıtay 22. HD"],
        "relevance_judgments": make_judgments(
            [d for d in aym if "2013-7449" in d],
            hd22[:2],
            idari_neg
        ),
        "contradictory_pairs": [{
            "doc_a": aym[0] if aym else "",
            "doc_b": hd22[0] if hd22 else "",
            "description": "Yargıtay 22. HD ile 7. HD arasında THY grev hakkı konusundaki görüş ayrılığı — AYM bireysel başvuruya konu olmuştur"
        }],
        "difficulty": "hard"
    })

    # =====================================================================
    # CLUSTER 16: İlk Derece — İdare Mahkemeleri
    # =====================================================================
    idare_mah = [d for d in doc_ids_by_pattern(manifest, court="İlk Derece")
                 if "idare" in d.lower() or "İdare" in d]

    queries.append({
        "query_id": next_id(),
        "query_text": "Kamu görevlisinin doğum sonrası yarım zamanlı çalışma hakkı",
        "query_type": "topical",
        "law_branch": "idari",
        "relevant_court": ["İdare Mahkemesi"],
        "relevance_judgments": make_judgments(idare_mah[:2], dan8[:1], idari_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "İdare Mahkemesinde iptal davası açma süresinin hesaplanması",
        "query_type": "procedural",
        "law_branch": "idari",
        "relevant_court": ["İdare Mahkemesi", "Danıştay 8. D."],
        "relevance_judgments": make_judgments(idare_mah[:2], dan8[:2], idari_neg),
        "difficulty": "easy"
    })

    # =====================================================================
    # CLUSTER 17: Tüketici / Fikri Haklar / İcra (Specialized Courts)
    # =====================================================================
    tuketici = [d for d in doc_ids_by_pattern(manifest, court="İlk Derece") if "tuketici" in d.lower() or "tüketici" in d.lower()]
    fikri = [d for d in doc_ids_by_pattern(manifest, court="İlk Derece") if "fikri" in d.lower()]
    icra = [d for d in doc_ids_by_pattern(manifest, court="İlk Derece") if "icra" in d.lower()]

    queries.append({
        "query_id": next_id(),
        "query_text": "Banka kartı ile yapılan dolandırıcılık işleminde bankanın sorumluluğu",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Tüketici Mahkemesi"],
        "relevance_judgments": make_judgments(tuketici, [], is_neg),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Bandrolsüz yayın satışı suçu ve ceza yargılaması",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Fikri ve Sınai Haklar Ceza Mahkemesi"],
        "relevance_judgments": make_judgments(fikri, [], ceza_neg),
        "difficulty": "easy"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "İcra takibine itiraz ve itirazın iptali davası prosedürü",
        "query_type": "procedural",
        "law_branch": "hukuk",
        "relevant_court": ["İcra Hukuk Mahkemesi"],
        "relevance_judgments": make_judgments(icra, bam_hd[:1], is_neg),
        "difficulty": "medium"
    })

    # =====================================================================
    # CLUSTER 18: Cross-Branch / Procedural
    # =====================================================================
    queries.append({
        "query_id": next_id(),
        "query_text": "Temyiz başvurusunun süresinde yapılıp yapılmadığının değerlendirilmesi",
        "query_type": "procedural",
        "law_branch": "cross_branch",
        "relevant_court": ["Yargıtay", "Danıştay"],
        "relevance_judgments": make_judgments(
            cd1[:1] + dan8[:1],
            hd9[:1] + vddk[:1],
            []
        ),
        "difficulty": "medium"
    })

    queries.append({
        "query_id": next_id(),
        "query_text": "Direnme kararı üzerine Hukuk Genel Kuruluna başvuru usulü",
        "query_type": "procedural",
        "law_branch": "hukuk",
        "relevant_court": ["HGK"],
        "relevance_judgments": make_judgments(hgk_all[:3], hd9[:1], is_neg),
        "difficulty": "easy"
    })

    # =====================================================================
    # CLUSTER 19: HGK — Borç İkrarı / Muvazaa
    # =====================================================================
    queries.append({
        "query_id": next_id(),
        "query_text": "Muvazaalı borç ikrarı senedinin geçersizliği ve dürüstlük kuralı",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["HGK"],
        "relevance_judgments": make_judgments(
            [d for d in hgk_all if "2015-1895" in d or "2015_1895" in d.replace("-", "_")][:1],
            hgk_all[:2],
            ceza_neg
        ),
        "difficulty": "medium"
    })

    # =====================================================================
    # Additional contradictory precedent queries
    # =====================================================================

    # Inter-daire conflict: 9. HD vs 22. HD on labor issues
    queries.append({
        "query_id": next_id(),
        "query_text": "Yargıtay 9. ve 22. Hukuk Dairesi arasında işçilik alacaklarına ilişkin içtihat farklılıkları",
        "query_type": "contradictory_precedent",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 9. HD", "Yargıtay 22. HD", "YİBBGK"],
        "relevance_judgments": make_judgments(yibbgk + hd9[:2] + hd22[:2], hgk_all[:1], ceza_neg),
        "contradictory_pairs": [{
            "doc_a": hd9[0] if hd9 else "",
            "doc_b": hd22[0] if hd22 else "",
            "description": "9. HD ve 22. HD arasındaki işçilik alacakları konusundaki sistematik içtihat farklılıkları"
        }],
        "difficulty": "easy"
    })

    # Bozma-direnme chain
    queries.append({
        "query_id": next_id(),
        "query_text": "Yargıtay bozma kararına karşı ilk derece mahkemesinin direnme kararı vermesi",
        "query_type": "contradictory_precedent",
        "law_branch": "hukuk",
        "relevant_court": ["HGK", "Yargıtay"],
        "relevance_judgments": make_judgments(hgk_all[:3], hd9[:1] + hd1_muvazaa[:1], ceza_neg),
        "contradictory_pairs": [{
            "doc_a": hgk_all[0] if hgk_all else "",
            "doc_b": hd9[0] if hd9 else "",
            "description": "HGK kararı ile bozma kararı veren daire arasındaki görüş ayrılığı"
        }],
        "difficulty": "medium"
    })

    # VDDK ısrar
    queries.append({
        "query_id": next_id(),
        "query_text": "Danıştay Vergi Dava Daireleri Kurulu ısrar kararına konu vergi uyuşmazlıkları",
        "query_type": "contradictory_precedent",
        "law_branch": "idari",
        "relevant_court": ["VDDK", "Danıştay"],
        "relevance_judgments": make_judgments(vddk, dan3[:1], idari_neg),
        "contradictory_pairs": [{
            "doc_a": vddk[0] if vddk else "",
            "doc_b": dan3[0] if dan3 else "",
            "description": "VDDK ile ilgili Danıştay dairesi arasındaki vergi hukuku yorumu farklılığı"
        }],
        "difficulty": "medium"
    })

    # Muris muvazaası İBK as contradiction resolution
    queries.append({
        "query_id": next_id(),
        "query_text": "Miras bırakanın muvazaalı işlemleri hakkında Yargıtay daireleri arasındaki görüş ayrılığı",
        "query_type": "contradictory_precedent",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay İBK", "Yargıtay 1. HD", "HGK"],
        "relevance_judgments": make_judgments(ibk_muvazaa + hd1_muvazaa[:2], hgk_muvazaa, ceza_neg),
        "contradictory_pairs": [{
            "doc_a": ibk_muvazaa[0] if ibk_muvazaa else "",
            "doc_b": hd1_muvazaa[0] if hd1_muvazaa else "",
            "description": "1974 İBK öncesi daireler arası görüş ayrılığı — İBK ile çözülmüştür"
        }],
        "difficulty": "medium"
    })

    # Danıştay inter-daire conflict on ihtiyati haciz
    queries.append({
        "query_id": next_id(),
        "query_text": "Danıştay daireleri arasında ihtiyati tahakkuk ve ihtiyati haciz konusundaki içtihat birliği",
        "query_type": "contradictory_precedent",
        "law_branch": "idari",
        "relevant_court": ["Danıştay İBK", "Danıştay 3. D.", "VDDK"],
        "relevance_judgments": make_judgments(dan_ibk + vddk[:1], dan3, idari_neg),
        "contradictory_pairs": [{
            "doc_a": dan3[0] if dan3 else "",
            "doc_b": dan7[0] if dan7 else "",
            "description": "Danıştay 3./9. Daire ile 4./7. Daire arasındaki ihtiyati haciz konusundaki görüş ayrılığı"
        }],
        "difficulty": "hard"
    })

    # =====================================================================
    # Additional queries to reach 50+
    # =====================================================================

    # BAM ceza
    queries.append({
        "query_id": next_id(),
        "query_text": "Tefecilik suçunun unsurları ve cezası",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["BAM"],
        "relevance_judgments": make_judgments(bam_cd[:1], cd1[:1], ceza_neg),
        "difficulty": "easy"
    })

    # 4. CD
    cd4 = doc_ids_by_pattern(manifest, daire="4. Ceza Dairesi")
    queries.append({
        "query_id": next_id(),
        "query_text": "TCK kapsamında tehdit ve şantaj suçlarının cezai yaptırımları",
        "query_type": "statute_application",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 4. CD"],
        "relevance_judgments": make_judgments(cd4, cd1[:1], ceza_neg),
        "difficulty": "medium"
    })

    # Danıştay 9. Daire
    dan9 = [d for d in doc_ids_by_pattern(manifest, daire="9. Daire") if "Danıştay" in d or "danistay" in d.lower() or "Başkanlığı" in d]
    if not dan9:
        dan9 = [d for d in manifest if not d["excluded"] and d["court"] == "Danıştay" and "9" in d.get("daire", "")]
        dan9 = [d["doc_id"] for d in dan9]

    queries.append({
        "query_id": next_id(),
        "query_text": "Vergi ziyaı cezası ve usulsüzlük cezasına karşı dava açma hakkı",
        "query_type": "topical",
        "law_branch": "idari",
        "relevant_court": ["Danıştay 9. D."],
        "relevance_judgments": make_judgments(dan9[:1] + dan3[:1], vddk[:1], idari_neg),
        "difficulty": "medium"
    })

    # 3. HD
    hd3 = doc_ids_by_pattern(manifest, daire="3. Hukuk Dairesi")
    queries.append({
        "query_id": next_id(),
        "query_text": "Kira bedelinin tespiti davası ve emsal kira araştırması",
        "query_type": "topical",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 3. HD"],
        "relevance_judgments": make_judgments(hd3, [], is_neg),
        "difficulty": "easy"
    })

    # Additional HGK
    queries.append({
        "query_id": next_id(),
        "query_text": "Hukuk Genel Kurulu kararlarında işçilik alacaklarına ilişkin emsal kararlar",
        "query_type": "case_law_search",
        "law_branch": "hukuk",
        "relevant_court": ["HGK"],
        "relevance_judgments": make_judgments(hgk_all[:3], hd9[:2], is_neg),
        "difficulty": "easy"
    })

    # BİM specific
    queries.append({
        "query_id": next_id(),
        "query_text": "Bölge İdare Mahkemesi vergi dava dairesi kararlarına karşı temyiz yolu",
        "query_type": "procedural",
        "law_branch": "idari",
        "relevant_court": ["BİM", "VDDK"],
        "relevance_judgments": make_judgments(bim, vddk[:1], idari_neg),
        "difficulty": "medium"
    })

    # Hard: colloquial ceza query
    queries.append({
        "query_id": next_id(),
        "query_text": "Birini bıçakladım ama o bana saldırdı, kendimi savundum, cezam ne olur?",
        "query_type": "topical",
        "law_branch": "ceza",
        "relevant_court": ["Yargıtay 1. CD"],
        "relevance_judgments": make_judgments(cd1[:3], [], ceza_neg),
        "difficulty": "hard"
    })

    # Hard: colloquial idari query
    queries.append({
        "query_id": next_id(),
        "query_text": "Devlet memuruyum, haksız yere soruşturma açıldı, ne yapabilirim?",
        "query_type": "topical",
        "law_branch": "idari",
        "relevant_court": ["İdare Mahkemesi", "Danıştay 8. D."],
        "relevance_judgments": make_judgments(idare_mah[:2], dan8[:2], idari_neg),
        "difficulty": "hard"
    })

    # Statute application: İYUK
    queries.append({
        "query_id": next_id(),
        "query_text": "İYUK m.7 uyarınca idari dava açma süresinin başlangıcı ve hesaplanması",
        "query_type": "statute_application",
        "law_branch": "idari",
        "relevant_court": ["İdare Mahkemesi", "Danıştay 8. D."],
        "relevance_judgments": make_judgments(idare_mah[:2], dan8[:2], idari_neg),
        "difficulty": "easy"
    })

    # Rekabet hukuku
    queries.append({
        "query_id": next_id(),
        "query_text": "Rekabet Kurulu kararının iptali davasında yargısal denetim kapsamı",
        "query_type": "topical",
        "law_branch": "idari",
        "relevant_court": ["İdare Mahkemesi", "Danıştay"],
        "relevance_judgments": make_judgments(idare_mah[:1], dan8[:1], idari_neg),
        "difficulty": "hard"
    })

    # Final statute application queries
    queries.append({
        "query_id": next_id(),
        "query_text": "TBK m.49 haksız fiil tazminatında zamanaşımı süresi ve başlangıcı",
        "query_type": "statute_application",
        "law_branch": "hukuk",
        "relevant_court": ["Yargıtay 1. HD", "HGK"],
        "relevance_judgments": make_judgments(hd1_muvazaa[:1], hgk_all[:1] + hd21[:1], ceza_neg),
        "difficulty": "medium"
    })

    return queries


def main():
    manifest = load_manifest()
    queries = build_queries(manifest)

    # Clean up: remove empty contradictory_pairs and empty doc_ids
    for q in queries:
        if "contradictory_pairs" in q:
            q["contradictory_pairs"] = [
                p for p in q["contradictory_pairs"]
                if p.get("doc_a") and p.get("doc_b")
            ]
            if not q["contradictory_pairs"]:
                del q["contradictory_pairs"]
        # Remove empty doc_ids from judgments
        q["relevance_judgments"] = [
            j for j in q["relevance_judgments"] if j["doc_id"]
        ]

    gold_standard = {
        "version": "1.0.0",
        "created_at": date.today().isoformat(),
        "queries": queries
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(gold_standard, f, ensure_ascii=False, indent=2)

    # Stats
    n_queries = len(queries)
    n_contradictory = sum(1 for q in queries if q.get("contradictory_pairs"))
    branches = {q["law_branch"] for q in queries}
    courts = set()
    for q in queries:
        for c in q.get("relevant_court", []):
            courts.add(c)
    difficulties = {}
    types = {}
    for q in queries:
        difficulties[q["difficulty"]] = difficulties.get(q["difficulty"], 0) + 1
        types[q["query_type"]] = types.get(q["query_type"], 0) + 1

    print(f"Generated {n_queries} queries → {OUTPUT}")
    print(f"Contradictory queries: {n_contradictory}")
    print(f"Branches: {sorted(branches)}")
    print(f"Courts: {len(courts)} unique")
    print(f"Difficulty: {difficulties}")
    print(f"Types: {types}")


if __name__ == "__main__":
    main()
