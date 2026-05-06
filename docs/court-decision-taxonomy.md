# Türk Mahkeme Karar Taksonomisi — Tam Matris

> **Bu dosya kanonik referanstır.** Türk yargı sistemine özgü `court` × `decision_authority` × `decision_type` × `disposition` ilişkileri tek doğru kaynak olarak burada tutulur. Epic 5.1, Epic 5.2, retrieval taksonomi modülleri ve LLM extraction prompt'ları bu dosyaya referans verir.
>
> **Hukuki dayanaklar:** HMK (Kanun 6100), CMK (Kanun 5271), İYUK (Kanun 2577), Anayasa, Yargıtay Kanunu (2797), Danıştay Kanunu (2575), Sayıştay Kanunu (6085), Anayasa Mahkemesinin Kuruluşu ve Yargılama Usulleri Hakkında Kanun (6216), Uyuşmazlık Mahkemesi Kanunu (2247), Avrupa İnsan Hakları Sözleşmesi + Mahkeme İçtüzüğü.

---

## 1. Eksenler

```
court              → forum (9 değer)
decision_authority → kararı veren organ (per-court enum)
decision_type      → prosedürel pozisyon (per-(court, authority) enum)
disposition        → hüküm fıkrası (per-(court, decision_type) enum)
interim_action     → prosedürel ara işlem (İade, Geri Çevirme vb. — disposition değil)
```

`court` mevcut 9-değer enum (Yargıtay · Danıştay · Sayıştay · AYM · Uyuşmazlık · AİHM · BAM · BİM · İlk Derece). Branş bilgisi `law_branch` field'ında (hukuk/ceza/idari/vergi/anayasa) saklı. Daire/şehir/numara `daire` (raw `court_name`) field'ında.

---

## 2. Yargıtay

### Decision Authorities

| authority | açıklama | kanuni dayanak |
|---|---|---|
| `daire` | Hukuk Daireleri / Ceza Daireleri (daire sayıları zamanla değişmiştir; aktif sayılar Resmî Gazete'de yayımlanan iş bölümü kararıyla belirlenir — 2024 itibarıyla 12 HD + 23 CD aktiftir) | Yargıtay K. 2797 |
| `hgk` | Hukuk Genel Kurulu | 2797 m.15 (HGK + CGK ortak madde) |
| `cgk` | Ceza Genel Kurulu | 2797 m.15 |
| `bgk` | Büyük Genel Kurul | 2797 m.16 |
| `baskanlar_kurulu` | Birinci Başkanlık Kurulu (idari/iş bölümü/disiplin) | 2797 m.18 |
| `ibhgk` | İçtihatları Birleştirme Hukuk Genel Kurulu | 2797 K. (HGK'nın içtihat birleştirme formasyonu) |
| `ibcgk` | İçtihatları Birleştirme Ceza Genel Kurulu | 2797 K. |
| `ibbgk` | İçtihatları Birleştirme Büyük Genel Kurulu | 2797 K. |
| `unknown` | court_name'den çıkarılamadı | — |

### (authority, decision_type) → disposition matrisi

| authority | decision_type | disposition |
|---|---|---|
| `daire` | `Temyiz` (HMK m.361 vd / CMK m.286 vd) | Onama · Bozma · Kısmen Onama · Kısmen Bozma · Düzeltilerek Onama (HMK) / Hukuka Aykırılığın Düzeltilerek Onanması (CMK m.303-304) · Düşme · Temyiz İsteminin Reddi (usulden — HMK m.367, CMK m.298) · Karar Düzeltme Kabulü (tarihsel — HUMK pre-2011) · Karar Düzeltme Reddi (tarihsel) · Diğer |
| `daire` | `Direnme Ön-İncelemesi` (HMK m.373/5 — 2020 değişikliği) | Direnme Yerinde Görülerek Düzeltildi · HGK'ya Gönderme · Diğer |
| `daire` | `Yargılamanın Yenilenmesi` (HMK m.374, CMK m.318) | Kabul · Ret · Diğer |
| `daire` | `Karar Düzeltme` (HUMK pre-2011-10-01) | Karar Düzeltme Kabulü · Karar Düzeltme Reddi · Diğer |
| `daire` (ceza) | `Olağanüstü İtiraz` (CMK m.308 — daire önce inceler, ısrar ederse CGK'ya) | Düzeltme · CGK'ya Gönderme · Ret · Diğer |
| `daire` (ceza) | `Kanun Yararına Bozma` (CMK m.309) | Kabul (Bozma) · Ret · Diğer |
| `daire` | `Diğer` | Diğer |
| `hgk` | `Direnme İncelemesi` (HMK m.373/2-3) | Direnme Yerinde Görüldü · Direnme Yerinde Görülmedi · Diğer |
| `hgk` | `İlk Derece Sıfatıyla` (Yargıtay K. m.46-47 — Cumhurbaşkanı/bakan/üst yargı yargılaması) | İlk Derece Hukuk enum'undan |
| `hgk` | `Diğer` | Diğer |
| `cgk` | `Direnme İncelemesi` (CMK m.307) | Direnme Yerinde Görüldü · Direnme Yerinde Görülmedi · Diğer |
| `cgk` | `Olağanüstü İtiraz` (CMK m.308 — daireden gelir) | Kabul (Bozma) · Ret · Diğer |
| `cgk` | `İlk Derece Sıfatıyla` (Yargıtay K. m.46-47) | İlk Derece Ceza enum'undan |
| `cgk` | `Diğer` | Diğer |
| `bgk` | `Diğer` | Diğer (BGK genelde idari/iç işleyiş; yargısal karar nadir) |
| `baskanlar_kurulu` | `İdari/Disiplin İtirazı` (Yargıtay K. m.18) | Kabul · Ret · Diğer |
| `baskanlar_kurulu` | `Görev/İş Bölümü Uyuşmazlığı` (m.16/2) | Diğer |
| `ibhgk` | `İçtihat Birleştirme` | İçtihat Birleştirildi · Birleştirmeye Yer Olmadığı · Diğer (içerik `decision_outcome_raw`'da) |
| `ibcgk` | `İçtihat Birleştirme` | aynı |
| `ibbgk` | `İçtihat Birleştirme` | aynı |
| `unknown` | `Diğer` | Diğer |

### Doktriner kurallar
- **Yargıtay `Direnme Kararı` ASLA üretmez** — direnme alt mahkemenindir; Yargıtay sadece `Direnme İncelemesi` (HGK/CGK) veya `Direnme Ön-İncelemesi` (daire — m.373/5 sonrası) yapar.
- **`İlk Derece Sıfatıyla` daire değil HGK/CGK görevidir** (Yargıtay K. m.46-47).
- **`Olağanüstü İtiraz` iki aşamalıdır:** önce karar veren daireye, ısrar ederse CGK'ya.

---

## 3. Danıştay

### Decision Authorities

| authority | açıklama | dayanak |
|---|---|---|
| `daire` | İdari ve vergi davalarına bakan daireler (sayılar tarihsel olarak değişmiştir; örn. 11. Daire 2014'te kaldırılmıştır) | Danıştay K. 2575 |
| `iddk` | İdari Dava Daireleri Kurulu | 2575 K. |
| `vddk` | Vergi Dava Daireleri Kurulu | 2575 K. |
| `daireler_kurulu` | Genel Kurul (idari/iç işleyiş) | 2575 K. |
| `baskanlar_kurulu` | Başkanlar Kurulu | 2575 K. |
| `ibk` | İçtihatları Birleştirme Kurulu | 2575 m.39-40 |
| `unknown` | — | — |

### (authority, decision_type) → disposition

| authority | decision_type | disposition |
|---|---|---|
| `daire` | `Temyiz` (İYUK m.46-49) | Onama · Bozma · Kısmen Onama · Kısmen Bozma · Düzeltilerek Onama · İade · Düşme · Temyiz İsteminin Reddi (usulden — ehliyet/süre/dilekçe/harç eksiklikleri) · Diğer |
| `daire` | `İlk Derece` (DK m.24, İYUK m.32 — Bakanlar Kurulu kararları, müşterek kararname vb.) | İptal · Ret · Kısmen İptal · Görevsizlik · Yetkisizlik · Düşme · Diğer |
| `daire` | `Yargılamanın Yenilenmesi` (İYUK m.53) | Kabul · Ret · Diğer |
| `daire` | `İtiraz` (İYUK m.45 mülga — pre-2016, 6545 sayılı K., BİM istinaf 20.07.2016'da fiilen başladı) | İtirazın Kabulü · İtirazın Reddi · Diğer |
| `daire` | `Kanun Yararına Bozma` (İYUK m.51) | Kabul (Bozma) · Ret · Diğer |
| `daire` | `Karar Düzeltme` (İYUK m.54 mülga — pre-2016) | Karar Düzeltme Kabulü · Karar Düzeltme Reddi · Diğer |
| `daire` | `Diğer` | Diğer |
| `iddk` | `Temyiz` (Danıştay K. 2575 — özellikle Danıştay'ın ilk derece olarak baktığı davalarda dairenin kararına karşı temyiz mercii olarak) | Onama · Bozma · Kısmen Onama · Kısmen Bozma · Düzeltilerek Onama · İade · Düşme · Temyiz İsteminin Reddi (usulden — ehliyet/süre/dilekçe/harç eksiklikleri) · Diğer |
| `iddk` | `Direnme İncelemesi` (İYUK m.49/4 — idari davalar; alt mahkemenin direnme kararı) | Direnme Yerinde Görüldü · Direnme Yerinde Görülmedi · Diğer |
| `iddk` | `Yargılamanın Yenilenmesi` (İYUK m.53) | Kabul · Ret · Diğer |
| `iddk` | `Karar Düzeltme` (İYUK m.54 mülga — pre-2016) | Karar Düzeltme Kabulü · Karar Düzeltme Reddi · Diğer |
| `iddk` | `Diğer` | Diğer |
| `vddk` | `Temyiz` (Danıştay K. 2575 — vergi dairelerinin kararlarına karşı temyiz) | Onama · Bozma · Kısmen Onama · Kısmen Bozma · Düzeltilerek Onama · İade · Düşme · Temyiz İsteminin Reddi (usulden — ehliyet/süre/dilekçe/harç eksiklikleri) · Diğer |
| `vddk` | `Direnme İncelemesi` (vergi davaları) | Direnme Yerinde Görüldü · Direnme Yerinde Görülmedi · Diğer |
| `vddk` | `Yargılamanın Yenilenmesi` (İYUK m.53) | Kabul · Ret · Diğer |
| `vddk` | `Karar Düzeltme` (İYUK m.54 mülga — pre-2016) | Karar Düzeltme Kabulü · Karar Düzeltme Reddi · Diğer |
| `vddk` | `Diğer` | Diğer |
| `daireler_kurulu` | `Diğer` | Diğer |
| `baskanlar_kurulu` | `İdari/İş Bölümü` (DK m.20) | Diğer |
| `ibk` | `İçtihat Birleştirme` (DK m.39-40) | İçtihat Birleştirildi · Birleştirmeye Yer Olmadığı · Diğer |
| `unknown` | `Diğer` | Diğer |

### Doktriner kurallar
- **`İlk Derece Sıfatıyla` Danıştay'da `İlk Derece`** olarak ayrı bir decision_type'tır — Yargıtay'daki "İlk Derece Sıfatıyla"nın muadili (DK m.24, İYUK m.32).
- **Tarihsel `İtiraz` ve `Karar Düzeltme` 2016 öncesi** (Kanun 6545 — fiilen 20.07.2016).

---

## 4. Sayıştay

### Decision Authorities

| authority | açıklama | dayanak |
|---|---|---|
| `daire` | 1.-8. Yargılama Dairesi | Sayıştay K. 6085 |
| `temyiz_kurulu` | Daire kararlarını temyizen inceler | 6085 m.55 vd. |
| `daireler_kurulu` | İçtihat oluşturma + bazı yargı kararları | 6085 m.27 |
| `genel_kurul` | İçtihat birleştirme + idari kararlar | 6085 m.25 (görevler), m.58 (içtihat birleştirme) |
| `unknown` | — | — |

### (authority, decision_type) → disposition

| authority | decision_type | disposition |
|---|---|---|
| `daire` (1.-8.) | `Hesap Yargılaması` (Anayasa m.160; 6085 m.48-50) | Tazmin Hükmü · Tazmin Hükmüne Yer Olmadığı · İlişiğin Olmadığı · Düşme · Diğer |
| `daire` | `Yargılamanın İadesi` (6085 m.56) | Kabul · Ret · Diğer |
| `temyiz_kurulu` | `Temyiz` (6085 m.55 vd.) | Onama · Bozma · Düzeltilerek Onama · Düşme · Diğer |
| `temyiz_kurulu` | `Karar Düzeltme` (6085 m.57) | Kabul · Ret · Diğer |
| `daireler_kurulu` | `Diğer` | Diğer |
| `genel_kurul` | `İçtihat Birleştirme` (6085 m.58) | İçtihat Birleştirildi · Birleştirmeye Yer Olmadığı · Diğer |
| `genel_kurul` | `Genel Uygunluk Bildirimi` (Anayasa m.164; 6085 m.41) | Diğer (yargı kararı değil ama korpus'ta yer alabilir — `court=Diğer` adayı) |
| `unknown` | `Diğer` | Diğer |

### Doktriner kurallar
- **Sayıştay yargısaldır** (Anayasa m.160) ama **denetim raporları idari**dir — denetim raporları `court=Diğer` etiketlenir.
- **"Beraat" terimi kullanılmaz**; doğrusu **`İlişiğin Olmadığı`** veya **`Tazmin Hükmüne Yer Olmadığı`** (mali yargılama, ceza yargılaması değil).
- **"İlam" decision_type değil** — kararın yazılı şeklidir; disposition `Tazmin Hükmü` zaten ilamı kapsar.
- **Sayıştay'da "Başkanlar Kurulu" diye bir yargısal organ yoktur** — Yargıtay/Danıştay'a benzer bir yapı Sayıştay'da bulunmaz; authority enum'una alınmamıştır.

---

## 5. Anayasa Mahkemesi (AYM)

### Decision Authorities

| authority | açıklama | dayanak |
|---|---|---|
| `genel_kurul` | 15 üye, Mahkeme Başkanı veya Başkanvekili başkanlığında | Anayasa m.149/1; 6216 m.21 |
| `bolum_1` | Birinci Bölüm (Başkanvekili + 6 üye) | m.149/1 |
| `bolum_2` | İkinci Bölüm | m.149/1 |
| `komisyon` | Bölümlerde kabul edilebilirlik komisyonları (3 üye) | m.149/2; 6216 m.48 |
| `unknown` | — | — |

### (authority, decision_type) → disposition

| authority | decision_type | disposition |
|---|---|---|
| `genel_kurul` | `Norm Denetimi` (Anayasa m.148/1 — kanun, CB kararnamesi, TBMM İçtüzüğü) | İptal · Ret · Kısmen İptal · İptal + Yürürlüğün Ertelenmesi (m.153/3) · İncelenmesine Yer Olmadığı · Düşme · Diğer |
| `genel_kurul` | `Anayasa Değişikliği Denetimi` (m.148/2 — sadece şekil) | İptal (Şekil Yönünden) · Ret · İncelenmesine Yer Olmadığı · Diğer |
| `genel_kurul` | `Siyasi Parti Kapatma` (m.69/4-6) | Kapatma · Devlet Yardımından Yoksun Bırakma (m.69/7) · İhtar (m.69/4 son) · Ret · Diğer |
| `genel_kurul` | `Siyasi Parti Mali Denetimi` (m.69/3, Kanun 2820 m.74-77) | Onama · Hazineye Gelir Kaydetme · Diğer |
| `genel_kurul` | `Yüce Divan` (m.148/6) | Mahkumiyet · Beraat · CVYO · Güvenlik Tedbiri · HAGB · Düşme · Görevsizlik · Diğer |
| `genel_kurul` | `Yasama Dokunulmazlığı` (m.85) | İptal · Ret · Diğer |
| `genel_kurul` | `Milletvekilliği Düşmesi İptali` (m.84/3) | İptal · Ret · Diğer |
| `genel_kurul` | `Bireysel Başvuru` (Bölüm sevki — 6216 K., esas inceleme aşamasında Genel Kurul'a sevk) | İhlal · İhlal Olmadığı · Kısmen İhlal · Yeniden Yargılama Yapılmasına (m.50/2) · Tazminat (m.50/2 son) · Düşme · Kabul Edilemezlik · Diğer |
| `genel_kurul` | `Tedbir Kararı` (6216 m.49/5; İçtüzük m.73) | Tedbir Kabulü · Tedbir Reddi · Diğer |
| `bolum_1` | `Bireysel Başvuru` (esas — m.149/3) | İhlal · İhlal Olmadığı · Kısmen İhlal · Yeniden Yargılama Yapılmasına (m.50/2) · Tazminat (m.50/2 son) · Düşme · Kabul Edilemezlik · Diğer |
| `bolum_1` | `Tedbir Kararı` (6216 m.49/5) | Tedbir Kabulü · Tedbir Reddi · Diğer |
| `bolum_2` | (bolum_1 ile aynı) | aynı |
| `komisyon` | `Bireysel Başvuru — Kabul Edilebilirlik` (6216 m.48) | Kabul Edilemezlik · Komisyonca Karar Verilemediği (Bölüme Aktarma) · Diğer |
| `unknown` | `Diğer` | Diğer |

### Doktriner / nüans kuralları
- **Bireysel Başvuru Genel Kurul'a sevkedilebilir** (6216 K. — esas inceleme aşamasında, ilkesel önemde dosyalar Bölümler tarafından Genel Kurul'a aktarılır).
- **Kabul Edilemezlik alt-türleri** (6216 m.48/2): konu/kişi/yer/zaman bakımından, başvuru yolları tüketilmemesi, açıkça dayanaktan yoksunluk. Disposition_subtype gerekirse ayrı tutulur.
- **"İhlal Yok" yanlış terim** — doğrusu **`İhlal Olmadığı`** (karar metinlerinde böyle).
- **Yüce Divan'da CMK atfen** HAGB, CVYO, Güvenlik Tedbiri dispositionları olası (6216 m.57 — Yüce Divan duruşma usulü).

---

## 6. Uyuşmazlık Mahkemesi

### Decision Authorities

| authority | açıklama | dayanak |
|---|---|---|
| `hukuk_bolumu` | Hukuk Bölümü | UMK 2247 |
| `idari_bolumu` | İdari Bölüm | UMK 2247 |
| `ceza_bolumu` | Ceza Bölümü | UMK 2247 |
| `genel` | Bölüm belirtilmemiş | — |
| `unknown` | — | — |

### (authority, decision_type) → disposition

| authority | decision_type | disposition |
|---|---|---|
| (her bölüm) | `Görev Uyuşmazlığı` (olumlu/olumsuz — UMK m.10, 14; yargı kolları arası: adli/idari/vergi) | Adli Yargı Görevli · İdari Yargı Görevli · Vergi Yargısı Görevli · Askeri Yargı Görevli (tarihsel pre-2017) · Başvurunun Reddi (UMK m.13, 19) · Diğer |
| (her bölüm) | `Hüküm Uyuşmazlığı` (UMK m.24-29) | Hüküm Uyuşmazlığının Giderilmesi · Başvurunun Reddi · Diğer |
| `genel` | `Diğer` | Diğer |
| `unknown` | `Diğer` | Diğer |

### Doktriner
- Uyuşmazlık Mahkemesi kararları **kesindir** (Anayasa m.158).
- **Görev Uyuşmazlığı** (mahkemeler arası görev) ile **Hüküm Uyuşmazlığı** (kesinleşmiş çelişkili kararlar) **farklı prosedürlerdir** (UMK m.10 vs m.24).

---

## 7. AİHM (Avrupa İnsan Hakları Mahkemesi)

### Decision Authorities

| authority | açıklama | dayanak |
|---|---|---|
| `tek_hakim` | Single Judge — sadece açıkça kabul edilemez başvuruları reddeder | ECHR Art. 27 |
| `komite` | Committee — 3 yargıç; well-established case-law | Art. 28 |
| `daire` | Chamber — 7 yargıç; Sections 1-5 | Art. 29 |
| `bd_paneli` | Grand Chamber referral panel — 5 yargıç | Art. 43 |
| `buyuk_daire` | Grand Chamber — 17 yargıç | Art. 30, 43 |
| `unknown` | — | — |

### (authority, decision_type) → disposition

| authority | decision_type | disposition |
|---|---|---|
| `tek_hakim` | `Kabul Edilebilirlik` (Art. 27) | Kabul Edilemez · Diğer |
| `komite` | `Kabul Edilebilirlik` (Art. 28) | Kabul Edilebilir · Kabul Edilemez · Diğer |
| `komite` | `Esas Karar` (well-established case-law — Art. 28/1-b) | İhlal Var · İhlal Yok · Kısmen İhlal · Diğer |
| `daire` | `Kabul Edilebilirlik` (Art. 29) | Kabul Edilebilir · Kabul Edilemez · Diğer |
| `daire` | `Esas Karar` (Art. 42) | İhlal Var · İhlal Yok · Kısmen İhlal · Diğer |
| `daire` | `Pilot Karar` (Rule 61) | Pilot Karar Verilmesi · Diğer |
| `daire` | `Dostane Çözüm` (Art. 39) | Karardan Düşme (Dostane Çözüm) · Diğer |
| `daire` | `Kayıttan Düşme` (Art. 37 — başvurucu davayı sürdürmüyor / mesele çözülmüş) | Karardan Düşme · Diğer |
| `daire` | `Tedbir Kararı` (Rule 39 — interim measure) | Tedbir Kabulü · Tedbir Reddi · Diğer |
| `daire` | `Adil Tazmin` (Art. 41 — sadece bağımsız karar olarak) | Tazmin Hükmü · Diğer |
| `daire` | `Yargı Yetkisinden Vazgeçme` (Art. 30 — Daire BD'ye sevkeder) | BD'ye Sevk · Diğer |
| `bd_paneli` | `Büyük Daire'ye Sevk Talebi İncelemesi` (Art. 43) | Talebin Kabulü · Talebin Reddi · Diğer |
| `buyuk_daire` | `Esas Karar` | İhlal Var · İhlal Yok · Kısmen İhlal · Diğer |
| `buyuk_daire` | `Pilot Karar` | Pilot Karar Verilmesi · Diğer |
| `buyuk_daire` | `Tavsiye Görüşü` (Protokol 16 — Türkiye onaylamadı, korpus'ta yok) | Görüş Verildi · Talep Reddi · Diğer |
| `buyuk_daire` | `Adil Tazmin` (Art. 41) | Tazmin Hükmü · Diğer |
| `unknown` | `Diğer` | Diğer |

### Doktriner
- **Komite oybirliği gerektirir**; sağlanamazsa Daire'ye gider (Art. 28/2).
- **Daire kararı 3 ay içinde Büyük Daire'ye sevkedilebilir** (Art. 43); Panel sevki incelerse kabul edebilir veya reddedebilir.
- **Tek Hakim** sadece **kabul edilemezlik** kararı verir; esas hakkında karar veremez.
- **Türkiye Protokol 16'yı imzaladı (2015) ama onaylamadı** — `Tavsiye Görüşü` decision_type'ı korpus'ta görülmez ama enum'da kalır.

---

## 8. BAM (Bölge Adliye Mahkemesi)

### Decision Authorities

| authority | açıklama |
|---|---|
| `daire` | Hukuk Dairesi veya Ceza Dairesi (branş `law_branch`'tan) |
| `unknown` | — |

### (authority, decision_type, law_branch) → disposition

| authority | decision_type | disposition (law_branch=hukuk) | disposition (law_branch=ceza) |
|---|---|---|---|
| `daire` | `İstinaf` | Esastan Ret (HMK m.353/1-b-1) · Düzelterek Esastan Ret (m.353/1-b-2) · Kaldırma ve Yeniden Esas Hakkında Karar (m.353/1-b-3) · Kaldırma ve Geri Gönderme (m.353/1-a) · Kısmen Kabul · Düşme · Diğer | Esastan Ret (CMK m.280/1-a) · Düzelterek Esastan Ret (m.280/1-b) · Bozma ve İade (m.280/1-c) · Yeniden Hüküm Kurma (m.280/1-d) · Düşme · Diğer |
| `daire` | `İlk Derece Sıfatıyla` (5235 sayılı K. m.34 — hâkim/yargı mensubu davaları) | İlk Derece Hukuk enum'u | İlk Derece Ceza enum'u |
| `daire` | `Yargılamanın Yenilenmesi` | Kabul · Ret · Diğer | aynı |
| `daire` (ceza) | `Tutuklama/Adli Kontrol İncelemesi` (CMK m.108/3, m.280) | — | Tutuklama Devamı · Tahliye · Adli Kontrol · Diğer |
| `daire` | `Diğer` | Diğer | Diğer |
| `unknown` | `Diğer` | Diğer | Diğer |

---

## 9. BİM (Bölge İdare Mahkemesi)

### Decision Authorities

| authority | açıklama |
|---|---|
| `daire` | İdari Dava Daireleri / Vergi Dava Daireleri |
| `unknown` | — |

### (authority, decision_type) → disposition

| authority | decision_type | disposition |
|---|---|---|
| `daire` | `İstinaf` (İYUK m.45 — post-2016 fiili) | İstinafın Reddi · İstinafın Kabulü (Kaldırma) · Düzelterek Ret · Kısmen Kabul · Diğer |
| `daire` | `İtiraz` (mülga pre-2016) | İtirazın Kabulü · İtirazın Reddi · Diğer |
| `daire` | `İlk Derece Sıfatıyla` | İlk Derece İdare enum'undan |
| `daire` | `Yürütmenin Durdurulması İtirazı` (İYUK m.27/6-7 — idare/vergi mahkemesi YD kararına itiraz mercii) | İtirazın Kabulü · İtirazın Reddi · Diğer |
| `daire` | `Yargılamanın Yenilenmesi` | Kabul · Ret · Diğer |
| `daire` | `Diğer` | Diğer |
| `unknown` | `Diğer` | Diğer |

---

## 10. İlk Derece

`law_branch` (hukuk / ceza / idari) decision_type setini ve authority'yi belirler. Authority = mahkeme türü.

### 10a. İlk Derece Hukuk (`law_branch=hukuk`)

#### Authorities

| authority | açıklama | dayanak |
|---|---|---|
| `asliye_hukuk` | Asliye Hukuk Mahkemesi (genel görev) | 5235 K. |
| `asliye_ticaret` | Asliye Ticaret Mahkemesi | 6102 K. (TTK) + 5235 K. |
| `aile` | Aile Mahkemesi | Kanun 4787 |
| `is` | İş Mahkemesi | Kanun 7036 |
| `tuketici` | Tüketici Mahkemesi | Kanun 6502 m.73 |
| `sulh_hukuk` | Sulh Hukuk Mahkemesi | 5235 K. |
| `kadastro` | Kadastro Mahkemesi | Kanun 3402 |
| `icra_hukuk` | İcra (Hukuk) Mahkemesi | İİK |
| `fshm` | Fikri ve Sınai Haklar Hukuk Mahkemesi | Kanun 5846 |
| `unknown` | — | — |

#### (authority, decision_type) → disposition

| decision_type | disposition |
|---|---|
| `Esas Karar` (HMK m.297-301) | Kabul · Ret · Kısmen Kabul · Davanın Reddi (Usulden) · Açılmamış Sayılma (m.150) · Feragat · Sulh · Davalının Kabulü (m.308) · Düşme · Görevsizlik · Yetkisizlik · Dava Şartı Yokluğundan Ret (m.114-115) · Diğer |
| `Direnme Kararı` (HMK m.373/2 — Yargıtay bozması sonrası) | Direnme · Diğer |
| `Çekişmesiz Yargı İşi` (HMK m.382-388 — gaiplik, mirasçılık, isim değişikliği, vesayet, tenfiz) | Kabul · Ret · Düşme · Diğer |
| `Ara Karar` | Diğer |
| `İhtiyati Tedbir` (m.389) | Tedbir Kabulü · Tedbir Reddi · Diğer |
| `İhtiyati Haciz` (İİK m.257) | Haciz Kabulü · Haciz Reddi · Diğer |
| `Delil Tespiti` (m.400-405) | Tespit · Talebin Reddi · Diğer |
| `Maddi Hata Düzeltme / Tashih / Tavzih` (m.304-305) | Kabul · Ret · Diğer |
| `Hâkimin Reddi / Çekilme` (m.36-43) | Kabul · Ret · Diğer |
| `İcra Hukuku Şikayeti` (İİK m.16, sadece `icra_hukuk` authority) | Kabul · Ret · Diğer |
| `İstihkak Davası` (İİK m.96-97, sadece `icra_hukuk` authority) | Kabul · Ret · Diğer |
| `Yargılamanın Yenilenmesi` (m.374) | Kabul · Ret · Diğer |
| `Diğer` | Diğer |

### 10b. İlk Derece Ceza (`law_branch=ceza`)

#### Authorities

| authority | açıklama | dayanak |
|---|---|---|
| `agir_ceza` | Ağır Ceza Mahkemesi | 5235 K. |
| `asliye_ceza` | Asliye Ceza Mahkemesi | 5235 K. |
| `cocuk` | Çocuk Mahkemesi / Çocuk Ağır Ceza Mahkemesi | Kanun 5395 |
| `sulh_ceza_hakimligi` | Sulh Ceza Hâkimliği (koruma tedbirleri, KYOK itirazı) | Kanun 6526 ile ihdas; CMK m.162 vd., m.173, m.267 |
| `infaz_hakimligi` | İnfaz Hâkimliği (ceza infaz uyuşmazlıkları) | Kanun 4675 |
| `unknown` | — | — |

#### (authority, decision_type) → disposition

| decision_type | applies to authorities | disposition |
|---|---|---|
| `Esas Karar` (CMK m.223) | `agir_ceza`, `asliye_ceza`, `cocuk` | Mahkumiyet · Beraat · Ceza Verilmesine Yer Olmadığı (CVYO — m.223/4) · Güvenlik Tedbiri Uygulanması (TCK m.32/2, m.31) · HAGB (m.231) · Erteleme (TCK m.51) · Adli Para Cezası · Düşme (m.223/8) · Davanın Reddi (m.223/7) · Görevsizlik · Yetkisizlik · Uyuşmazlık Çıkarma (m.6) · Diğer |
| `Direnme Kararı` (CMK m.307/3) | `agir_ceza`, `asliye_ceza` | Direnme · Diğer |
| `Bağımsız Müsadere` (CMK m.256, TCK m.54-55) | `agir_ceza`, `asliye_ceza` | Müsadere · Talebin Reddi · Diğer |
| `Tutuklama/Adli Kontrol` (CMK m.100, 109) | `sulh_ceza_hakimligi` | Tutuklama · Adli Kontrol · Tahliye · Talebin Reddi · Diğer |
| `Koruma Tedbirleri` (arama, elkoyma, iletişim tespiti — CMK m.116, 119, 135) | `sulh_ceza_hakimligi` | Talebin Kabulü · Talebin Reddi · Diğer |
| `KYOK İtirazı` (CMK m.173) | `sulh_ceza_hakimligi` | İtirazın Kabulü · İtirazın Reddi · Diğer |
| `İtiraz İncelemesi` (CMK m.267-271 — Asliye→Ağır Ceza, Sulh→Asliye; HAGB, tutuklama, adli kontrol itirazları) | `agir_ceza`, `asliye_ceza` | İtirazın Kabulü · İtirazın Reddi · Diğer |
| `İnfaz Hukuku Kararı` (5275 K. — şartla tahliye, disiplin cezası itirazı, infazın ertelenmesi/geri bırakılması) | `infaz_hakimligi` | Talebin Kabulü · Talebin Reddi · Diğer |
| `Yargılamanın Yenilenmesi` (CMK m.311) | `agir_ceza`, `asliye_ceza`, `cocuk` | Kabul · Ret · Diğer |
| `İade Talebi` (Kanun 6706 — uluslararası iade) | `agir_ceza` | Kabul · Ret · Diğer |
| `Ara Karar` | tüm | Diğer |
| `Diğer` | tüm | Diğer |

### 10c. İlk Derece İdare/Vergi (`law_branch=idari` veya `vergi`)

#### Authorities

| authority | açıklama |
|---|---|
| `idare` | İdare Mahkemesi |
| `vergi` | Vergi Mahkemesi |
| `unknown` | — |

#### (authority, decision_type) → disposition

| decision_type | disposition |
|---|---|
| `İptal Davası` (İYUK m.2/1-a) | İptal · Ret · Kısmen İptal · Görevsizlik · Yetkisizlik · Diğer |
| `Tam Yargı Davası` (m.2/1-b) | Tam Yargı Kabulü · Tam Yargı Reddi · Kısmen Kabul · Diğer |
| `Yürütmenin Durdurulması` (m.27) | YD Kabulü · YD Reddi · Diğer |
| `Vergi Davası` (sadece `vergi` authority) | İptal · Ret · Kısmen İptal · Tasdik · Tadilen Tasdik · Diğer |
| `Direnme Kararı` (İYUK m.49/4 — Danıştay bozması sonrası) | Direnme · Diğer |
| `Yargılamanın Yenilenmesi` (m.53) | Kabul · Ret · Diğer |
| `Diğer` | Diğer |

---

## 11. `interim_action` ekseni (disposition'dan ayrı)

Disposition esas hükmü taşır; `interim_action` prosedürel ara işlemi taşır. Ana karar kategorisi değildir, ek alandır.

| value | açıklama | uygulanır |
|---|---|---|
| `iade` | Dosya yetersiz/eksik için ilk derece mahkemesine geri | Yargıtay/Danıştay temyiz |
| `geri_cevirme` | Usuli yetersiz başvuru ret | Yargıtay/Danıştay temyiz |
| `birlestirme` | Davaların birleştirilmesi (HMK m.166) | tüm yargı |
| `ayirma` | Davaların ayrılması | tüm yargı |
| `gorevsizlik_gondermesi` | Görevsizlik kararı sonrası dosya transferi | tüm yargı |
| `yetkisizlik_gondermesi` | Yetkisizlik sonrası dosya transferi | tüm yargı |
| `null` | yok | varsayılan |

---

## 12. Validation kuralları (kod implementasyonu için)

### Yapısal kombinasyon yasakları

1. **Direnme yasağı:** `court ∈ {Yargıtay, Danıştay}` ve `decision_type='Direnme Kararı'` → forbidden. Bu mahkemeler `Direnme İncelemesi` veya `Direnme Ön-İncelemesi` yapar; `Direnme Kararı` alt mahkemenindir.
2. **(daire, Direnme İncelemesi) Yargıtay/Danıştay'da forbidden** — yalnız HGK/CGK veya İDDK/VDDK'da olur. Daire'de ancak `Direnme Ön-İncelemesi` (HMK m.373/5) olur.
3. **(daire, İlk Derece Sıfatıyla) Yargıtay'da forbidden** — yalnız HGK/CGK görevi (Yargıtay K. m.46-47).
4. **`İçtihat Birleştirme` yalnız `ibhgk`/`ibcgk`/`ibbgk`/`ibk`/`genel_kurul` (Sayıştay) authority'lerinde olur.**
5. **AİHM: `tek_hakim` authority sadece `Kabul Edilebilirlik` decision_type'ında (Kabul Edilemez disposition).** Esas karar veremez.
6. **AYM: `komisyon` authority sadece `Bireysel Başvuru — Kabul Edilebilirlik`'tedir;** esas karar veremez.

### Tarih-bazlı uyarılar

7. **`Karar Düzeltme` (Yargıtay civil) `decision_date >= 2011-10-01`** → flag (HMK Kanun 6100 ile mülga).
8. **`Karar Düzeltme` (Yargıtay ceza) tüm tarihler** → flag (CMK ordinary route olarak hiç tanımlamaz).
9. **`Karar Düzeltme` (Danıştay) ve `İtiraz` (Danıştay) `decision_date >= 2016-07-20`** → flag (Kanun 6545 ile mülga; 6545 2014'te çıktı, BİM istinaf 20.07.2016'da fiilen başladı).
10. **`İtiraz` (BİM) `decision_date >= 2016-07-20`** → flag.
11. **`Askeri Yargı Görevli` (Uyuşmazlık) `decision_date >= 2017-04-16`** → flag (askeri yargı 2017 referandumuyla kaldırıldı — Anayasa m.145, m.156-157).

### Veri kalitesi kuralları

12. **KYOK** (Kovuşturmaya Yer Olmadığı Kararı — CMK m.172): savcılık işlemi, mahkeme kararı değil → `court=Diğer` veya korpustan dışla.
13. **Sayıştay denetim raporları:** yargısal değil → `court=Diğer`.

---

## 13. Tarihsel / lağvedilmiş court'lar (`court=Diğer` çuvalı)

Korpus 1974-2026 dönemini kapsadığı için tarihsel kararlar görülür:

| court | dönem | not |
|---|---|---|
| Askeri Yargıtay | 1982-2017 | 2017 anayasa değişikliği (m.145, m.156-157 mülga) ile kaldırıldı |
| Askeri Yüksek İdare Mahkemesi (AYİM) | -2017 | Aynı tarih |
| Devlet Güvenlik Mahkemeleri (DGM) | 1973-2004 | 2004'te ihtisas ceza mahkemelerine dönüştü |
| Sıkıyönetim Mahkemeleri | 1971-1983 | dönemsel |
| Yüksek Seçim Kurulu (YSK) | — | Yargısal değil; bazı kararları yargısal nitelik taşır → tartışmalı |
| Hâkimler ve Savcılar Kurulu (HSK) | — | yargı yönetimi, yargısal değil |
| Adli Tıp Kurumu | — | bilirkişilik |

Bu kararlar `court=Diğer` etiketlenir; özel decision_type/authority/disposition yapısı kurulmaz.

---

## 14. Kanuni atıf indeksi

| Mevzuat | No | Kapsam |
|---|---|---|
| Anayasa | — | m.69 (parti), m.84-85 (mv. statüsü), m.148-153 (AYM), m.158 (Uyuşmazlık), m.160 (Sayıştay) |
| HMK | 6100 | m.297 vd. (esas karar), m.353 (BAM hukuk), m.361 vd. (temyiz), m.373 (direnme), m.382 vd. (çekişmesiz yargı) |
| CMK | 5271 | m.223 (esas karar), m.231 (HAGB), m.267-271 (itiraz), m.280 (BAM ceza), m.286 vd. (temyiz), m.307-308 (direnme/olağanüstü itiraz), m.309 (kanun yararına bozma), m.311 (yargılama yenilenme) |
| İYUK | 2577 | m.2 (dava türleri), m.27 (YD), m.32 (Danıştay ilk derece), m.45 (BİM istinaf), m.46-49 (temyiz), m.51 (kanun yararına bozma), m.53 (yargılama yenilenme) |
| Yargıtay K. | 2797 | m.15 (HGK + CGK), m.16 (BGK), m.18 (Birinci Başkanlık Kurulu), m.46-47 (ilk derece sıfatıyla görev) |
| Danıştay K. | 2575 | m.24 (ilk derece görev), m.39-40 (içtihat birleştirme); başkanlar kurulu / İDDK / VDDK için spesifik madde numarası bu dökümanda doğrulanmamıştır — `2575 K.` referansı yeterli sayılır |
| Sayıştay K. | 6085 | m.25 (Genel Kurul görevleri), m.27 (Daireler Kurulu), m.41 (Genel Uygunluk Bildirimi), m.48-50 (hesap yargılaması), m.55 vd. (temyiz), m.56 (yargılama iadesi), m.57 (karar düzeltme), m.58 (içtihat birleştirme). **Sayıştay'da "Başkanlar Kurulu" diye bir organ yoktur.** |
| AYM K. | 6216 | m.21 (Genel Kurul), m.45 (bireysel başvuru hakkı), m.48 (kabul edilebilirlik), m.49 (esas inceleme; m.49/5 tedbir), m.50 (karar; m.50/2 yeniden yargılama), m.57 (Yüce Divan duruşma usulü) |
| Uyuşmazlık M. K. | 2247 | m.10, 14, 17 (görev/yargı yolu), m.24-29 (hüküm uyuşmazlığı) |
| 5235 (Adli Yargı) | 5235 | mahkeme türleri |
| 5275 (CGTİHK) | 5275 | İnfaz Hâkimliği |
| 6545 | 6545 | İdari yargı 2014 reformu (m.45/54 mülga) |
| 6100 (HMK gerekçe) | — | HUMK karar düzeltme mülga 2011 |
| ECHR (AİHS) | — | Art. 27, 28, 29, 30, 35, 37, 39, 41, 42, 43, 44 |
| ECtHR Rules of Court | — | Rule 39 (interim), Rule 61 (pilot) |
| Protokol 16 | — | Tavsiye görüşü (Türkiye onaylamadı) |

---

## Notlar

1. **`law_branch` ve `court` birlikte court_type'ı belirler.** Prompt'taki 13-değer `court_type` enum'u bu iki alandan deterministik türetilir; ayrı bir field olarak tutulmasına gerek yok (bkz. `eval/scripts/build_corpus_manifest.py:infer_court_level` paraleli).
2. **`decision_authority` LLM tarafından üretilmez** — `daire` (Mongo `court_name`) ve LLM'in çıkardığı `court` free-text alanından **post-process regex** ile türetilir.
3. **`disposition` Epic 5.1'in (Disposition Extraction) sorumluluğundadır** — bu doküman sadece kanonik enum'u tanımlar; doldurma akışı Epic 5.1'de.
4. **Tarihsel decision_type'lar enum'da tutulur** (1974-2026 korpusu) — tarih-bazlı validation kuralları (m.12) ile yanlış kullanım yakalanır.
5. **İhtisas mahkemeleri authority olarak modellenmiştir** (örn. `aile`, `is`, `tuketici`, `sulh_ceza_hakimligi`, `infaz_hakimligi`) — court=İlk Derece, authority=ihtisas mahkemesi türü.
