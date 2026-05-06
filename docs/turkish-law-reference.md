# Turkish Law System Reference — LLM Cheat Sheet

> This document is a structured reference for any LLM or AI agent working with Turkish legal data.
> It covers the full architecture of the Turkish legal system: court hierarchy, sources of law, citation formats, appeal flows, key terminology, and critical nuances for retrieval systems.
>
> **Legal tradition:** Civil law (continental European model). Heavily influenced by Swiss (civil code), German (commercial code), Italian (criminal code), and French (administrative law) traditions.
>
> **Important caveat:** Yargıtay chamber assignments change annually via the Birinci Başkanlar Kurulu's iş bölümü kararı. Monetary thresholds (kesinlik sınırı) are updated yearly. Always verify against the latest Resmî Gazete.
>
> **Version note:** v3 — incorporates lawyer corrections from v2 (26/06/2025 Yargıtay iş bölümü, updated BAM/BİM counts, 8th Judicial Package changes) with chamber jurisdiction tables condensed for retrieval system use.

---

## 1. Court Hierarchy (Yargı Teşkilatı)

Turkey's judiciary spans **five pillars** — four domestic plus the international layer (AİHM, by Anayasa m.90).
The diagram below also shows the system's `court_level` integer (1–5) used in retrieval/graph code:

```
┌─────────────────────────────────────────────────────────────────────────┐  level 5
│             AİHM (Avrupa İnsan Hakları Mahkemesi — Strasbourg)          │  ULUSLARARASI
│         Reachable after AYM bireysel başvuru (Anayasa m.90)             │
└─────────────────────────────────────────────────────────────────────────┘
                                   ▲
┌────────────────────────────────────────┐  ┌─────────────────────────┐    level 4
│         ANAYASA MAHKEMESİ              │  │  UYUŞMAZLIK MAHKEMESİ   │    ANAYASAL +
│       (Constitutional Court)           │  │  (Jurisdictional        │    UYUŞMAZLIK
│ Norm review, bireysel başvuru          │  │   Disputes — final)     │
└────────────────────────────────────────┘  └─────────────────────────┘

┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────┐  level 3
│     ADLİ YARGI       │  │     İDARİ YARGI      │  │     SAYIŞTAY     │  TEMYİZ
│ (Ordinary Judiciary) │  │ (Admin. Judiciary)   │  │ (Hesap Yargısı —  │
├──────────────────────┤  ├──────────────────────┤  │  audit / fiscal) │
│  YARGITAY            │  │  DANIŞTAY            │  │  - 8 daireler    │
│  (Cassation)         │  │  (Council of State)  │  │  - Temyiz Kurulu │
│  - Hukuk Daireleri   │  │  - İdari Dava Dair.  │  └──────────────────┘
│  - Ceza Daireleri    │  │  - Vergi Dava Dair.  │
│  - HGK / CGK         │  │  - İDDK / VDDK       │
│  - İçtihadı Birleşt. │  │  - İçtihadı Birleşt. │
│          ▲           │  │          ▲           │
│     TEMYİZ           │  │     TEMYİZ           │
│          │           │  │          │           │
│  BÖLGE ADLİYE        │  │  BÖLGE İDARE         │                         level 2
│  MAHKEMELERİ (BAM)   │  │  MAHKEMELERİ (BİM)  │                         İSTİNAF
│  - 17 across Turkey  │  │  - 12 across Turkey  │
│  - Operational 2016  │  │                      │
│          ▲           │  │          ▲           │
│     İSTİNAF          │  │     İSTİNAF          │
│          │           │  │          │           │
│  İLK DERECE          │  │  İLK DERECE          │                         level 1
│  (First Instance)    │  │  - İdare Mahkemesi   │                         İLK DERECE
│  See table below     │  │  - Vergi Mahkemesi   │
└──────────────────────┘  └──────────────────────┘
```

**`court_level` mapping (used by `infer_court_level` and Neo4j `_COURT_TYPE_META`):**

| Level | Pillar | Mahkemeler |
|---:|---|---|
| 1 | İlk Derece | Asliye, sulh, idare, vergi, iş, ticaret, tüketici, fikri sınai, asliye ceza, icra hukuk |
| 2 | İstinaf | BAM, BİM |
| 3 | Temyiz | Yargıtay (HD/CD + HGK/CGK + İBK), Danıştay (D + İDDK/VDDK + İBK), Sayıştay (daireler) |
| 4 | Anayasal + Uyuşmazlık | AYM, Uyuşmazlık Mahkemesi |
| 5 | Uluslararası | AİHM |

> **Note:** HGK/CGK direnme kararları ve İBK kararları semantik olarak temyiz dairesinden farklı bağlayıcılığa sahip; `court_level` bunları tier 3'te birleştiriyor. Daha ince ayrım (binding scope, daire kimliği) `court_name` üzerinden ayrı property olarak modellenecek.

### 1.1 Adli Yargı (Ordinary Judiciary)

#### 1.1.1 İlk Derece Mahkemeleri (First Instance Courts)


| Turkish Name                        | English                                | Jurisdiction                                                                                                                              | Judge(s)                    |
| ----------------------------------- | -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------------- |
| **Asliye Hukuk Mahkemesi**          | Court of General Civil Jurisdiction    | Default civil court for cases not assigned elsewhere                                                                                      | Single judge                |
| **Asliye Ceza Mahkemesi**           | Court of General Criminal Jurisdiction | All crimes punishable by up to 10 years imprisonment and/or judicial fine (residual jurisdiction)                                          | Single judge                |
| **Ağır Ceza Mahkemesi**             | Heavy/Serious Criminal Court (Assize)  | Serious crimes: homicide, sexual assault, organized crime, terror, 10+ year sentences                                                     | Panel of 3 (başkan + 2 üye) |
| **Sulh Hukuk Mahkemesi**            | Civil Court of Peace                   | Small-value civil disputes, voluntary jurisdiction (çekişmesiz yargı), guardianship (vesayet), estate                                     | Single judge                |
| **Sulh Ceza Hâkimliği**             | Criminal Judgeship of Peace            | NOT a trial court. Search/seizure warrants, detention orders (tutuklama), objections to non-prosecution                                   | Single judge                |
| **İş Mahkemesi**                    | Labor Court                            | Employment disputes. Mandatory mediation (arabuluculuk) required before filing (Law No. 7036)                                             | Single judge                |
| **Aile Mahkemesi**                  | Family Court                           | Divorce, custody, alimony, domestic violence (Law No. 4787)                                                                               | Single judge                |
| **Asliye Ticaret Mahkemesi**        | Commercial Court                       | Company law, insurance, banking, maritime, negotiable instruments. Where no dedicated court exists, Asliye Hukuk sits as commercial court | Single judge                |
| **Tüketici Mahkemesi**              | Consumer Court                         | Consumer disputes above the hakem heyeti threshold                                                                                        | Single judge                |
| **Kadastro Mahkemesi**              | Cadastral Court                        | Land registration and cadastral survey disputes                                                                                           | Single judge                |
| **İcra Mahkemesi**                  | Enforcement Court                      | Complaints about enforcement proceedings. İcra Hukuk (civil) and İcra Ceza (criminal)                                                     | Single judge                |
| **Fikrî ve Sınaî Haklar Mahkemesi** | IP Court                               | Patent, trademark, copyright disputes (major cities only)                                                                                 | Single judge                |
| **Fikri ve Sınai Haklar Ceza Mahkemesi** | IP Criminal Court                | Criminal offenses related to intellectual/industrial property: counterfeiting, piracy, patent infringement (major cities only)              | Single judge                |
| **Çocuk Mahkemesi**                 | Juvenile Court                         | Crimes committed by children (ages 12–18) punishable by up to 10 years imprisonment (Law No. 5395)                                        | Single judge                |
| **Çocuk Ağır Ceza Mahkemesi**       | Juvenile Heavy Criminal Court          | Crimes committed by children (ages 12–18) punishable by 10+ years imprisonment (Law No. 5395)                                             | Panel of 3 (başkan + 2 üye) |

#### 1.1.2 Bölge Adliye Mahkemeleri (BAM) — Regional Courts of Appeal

- Created by Law No. 5235 (2004), **operational since 20 July 2016**
- Before 2016, Turkey had NO intermediate appellate courts — cases went directly from first instance to Yargıtay
- **17 BAMs** across Turkey: İstanbul, Ankara, İzmir, Bursa, Samsun, Konya, Antalya, Gaziantep, Erzurum, Diyarbakır, Sakarya, Trabzon, Adana, Kayseri, Van, Denizli (operational Sept 2024), Tekirdağ (operational 2024)
- Malatya BAM established by decree (June 2022) but **not yet operational** — delayed due to February 2023 earthquake, foundation laid June 2025
- Each BAM has multiple civil (hukuk) and criminal (ceza) chambers (daireler)
- BAMs conduct BOTH procedural and substantive review (can re-examine evidence)

#### 1.1.3 Yargıtay (Court of Cassation)

- Highest court of the adli yargı branch. Hears temyiz (cassation) appeals from BAM decisions.
- 12 Hukuk Daireleri + 12 Ceza Daireleri + Genel Kurullar
- **→ Detailed chamber breakdown: see [Section 2](#2-yargıtay-court-of-cassation--adli-yargı-yüksek-mahkemesi)**

### 1.2 İdari Yargı (Administrative Judiciary)

#### 1.2.1 İlk Derece Mahkemeleri

| Turkish Name              | English              | Jurisdiction                                                                          | Judge(s) |
| ------------------------- | -------------------- | ------------------------------------------------------------------------------------- | -------- |
| **İdare Mahkemesi**       | Administrative Court | İptal davası (annulment) and tam yargı davası (full remedy/damages) against administrative acts | Panel    |
| **Vergi Mahkemesi**       | Tax Court            | Tax disputes                                                                          | Panel    |

#### 1.2.2 Bölge İdare Mahkemeleri (BİM) — Regional Administrative Courts of Appeal

- Governed by Law No. 2576
- **12 BİMs** across Turkey (expanded from 9 in May 2025): İstanbul, Ankara, İzmir, Adana, Bursa, Erzurum, Gaziantep, Konya, Samsun, Antalya (2025), Diyarbakır (2025), Kayseri (2025)
- Appellate court for İdare Mahkemesi and Vergi Mahkemesi decisions
- Each BİM has multiple chambers (idari dava daireleri and vergi dava daireleri)
- Appeals from BİM go to Danıştay (Council of State) via temyiz

#### 1.2.3 Danıştay (Council of State)

- Highest court of the idari yargı branch. Hears temyiz appeals from BİM decisions. Also has first-instance and advisory jurisdiction.
- Vergi Dava Daireleri + İdari Dava Daireleri + İstişari (advisory) function
- **→ Detailed chamber breakdown: see [Section 3](#3-danıştay-council-of-state--i̇dari-yargı-yüksek-mahkemesi)**

### 1.3 Anayasa Mahkemesi (Constitutional Court)

- 15 members (reduced from 17 by 2017 referendum)
- **Jurisdiction:**
  - Soyut norm denetimi (abstract norm review) — laws challenged by 1/5 of parliament
  - Somut norm denetimi (concrete norm review) — referral from a court during a case
  - Bireysel başvuru (individual application) — introduced 2012, modeled on ECHR. Available after ALL ordinary remedies are exhausted
  - Yüce Divan (High Court) — trial of President, ministers, high judges
- Two sections (bölüm) for individual applications, plenary (Genel Kurul) for norm review

---

## 2. Yargıtay (Court of Cassation — Adli Yargı Yüksek Mahkemesi)

> **Critical:** Chamber assignments are set by the Yargıtay Büyük Genel Kurulu. Below reflects the **26/06/2025 tarih ve 1 sayılı karar**. Only **12 Hukuk Daireleri** and **12 Ceza Daireleri** remain active.

### 2.1 Hukuk Daireleri (Civil Chambers)

Four ihtisas alanları: Medeni Hukuk, Gayrimenkul Hukuku, Borçlar-Ticaret Hukuku, İş ve Sosyal Güvenlik Hukuku.

| Daire | Topic Keywords |
|-------|---------------|
| **1. HD** | Gayrimenkul mülkiyeti, tapu sicili, kadastro, tapu iptali ve tescil, muris muvazaası |
| **2. HD** | Aile hukuku — boşanma, velayet, nafaka, soybağı, evlat edinme, mal rejimi, vesayet |
| **3. HD** | Sözleşme, tüketici, sebepsiz zenginleşme, kira, adi ortaklık, kusursuz sorumluluk |
| **4. HD** | Haksız fiil tazminatı, trafik kazası, sigorta (kasko/trafik/can-hayat), manevi tazminat, basın |
| **5. HD** | Kamulaştırma, kamulaştırmasız el atma, kat mülkiyeti, tapu sicili tazminatı, afet riski |
| **6. HD** | Eser sözleşmeleri, inşaat, kooperatif, konkordato, iflas ve iflasın ertelenmesi |
| **7. HD** | Sınırlı ayni haklar, önalım, taşınmaz satış vaadi, geçit/mecra hakkı, miras, izale-i şüyu |
| **8. HD** | Kadastro (ilk tesis, orman, 2/B, imar), dernekler, vakıflar, tüzel kişiler |
| **9. HD** | İş hukuku — işe iade, kıdem/ihbar tazminatı, fazla çalışma, sendika, toplu iş sözleşmesi |
| **10. HD** | Sosyal güvenlik — SGK rücu, hizmet tespiti, iş kazası/meslek hastalığı, 5510 s.K. |
| **11. HD** | Ticaret, şirketler, kıymetli evrak (çek/bono/poliçe), haksız rekabet, sınai mülkiyet, deniz ticareti, bankacılık |
| **12. HD** | İcra ve iflas — şikâyet/itiraz, istihkak, menfi tespit, tasarrufun iptali, kira tahliye (icra yoluyla) |

**Closed/merged chambers:**

- 13. HD (Tüketici) → 3. HD; 14. HD (Tüketici overflow) → 3. HD / 11. HD; 15. HD (Ticaret) → 11. HD / 6. HD
- 18. HD → closed; 19. HD (Bankacılık) → 11. HD; 20. HD (Orman/Kadastro) → 8. HD; 21. HD (İş kazası) → 10. HD; 22. HD (İş overflow) → 9. HD; 23. HD (Şirketler) → 11. HD

### 2.2 Ceza Daireleri (Criminal Chambers)

> Only **12 Ceza Daireleri** remain active; 13. CD closed/merged into 3. CD.

| Daire | Topic Keywords |
|-------|---------------|
| **1. CD** | Kasten öldürme, kasten yaralama, infaz uyuşmazlıkları |
| **2. CD** | Hırsızlık, konut dokunulmazlığı, karşılıksız yararlanma |
| **3. CD** | Terör, devlet güvenliği, soykırım, insanlığa karşı suçlar, suçtan kaynaklanan malvarlığı aklama |
| **4. CD** | Göçmen kaçakçılığı, hakaret, çevre/imar kirliliği, suç örgütü, Cumhurbaşkanına hakaret |
| **5. CD** | Kamu görevlisi suçları — zimmet, irtikâp, rüşvet, görevi kötüye kullanma, ihaleye fesat |
| **6. CD** | Yağma, tehdit, şantaj, mala zarar verme |
| **7. CD** | Kaçakçılık, bankacılık suçları, özel ceza kanunları (catch-all) |
| **8. CD** | İşkence, hürriyet, uyuşturucu (1/2), silah, genel güvenlik, genel TCK catch-all |
| **9. CD** | Cinsel suçlar — cinsel saldırı, çocuk istismarı, cinsel taciz |
| **10. CD** | Uyuşturucu (1/2), kamusal sağlık, zehirli madde |
| **11. CD** | Dolandırıcılık, belgede sahtecilik, bilişim suçları, banka/kredi kartı |
| **12. CD** | Taksirle öldürme/yaralama, trafik güvenliği, özel hayat, kişisel veriler, VUK |

### 2.3 Genel Kurullar & İçtihadı Birleştirme

#### Genel Kurullar (General Assemblies)


| Kurul                        | Composition                                   | Function                                                                                                             |
| ---------------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| **Hukuk Genel Kurulu (HGK)** | Presidents + members of all civil chambers    | Hears direnme (resistance) cases in civil matters. Final and binding. Meets weekly (typically Wednesdays).           |
| **Ceza Genel Kurulu (CGK)**  | Presidents + members of all criminal chambers | Hears direnme cases in criminal matters. Final and binding. Meets weekly (typically Tuesdays).                       |
| **Büyük Genel Kurul**        | ALL Yargıtay members                          | Administrative: elects Birinci Başkan, Başkanvekilleri, Cumhuriyet Başsavcısı; approves annual iş bölümü kararı. Does NOT hear cases (except İBBGK level). |

#### HGK / CGK Süreci (Direnme Yolu)

```
Daire → bozma kararı verir (reversal)
    ↓
İlk derece / BAM iki seçenek:
    ├─ UYMA → bozmaya uyar, yeniden yargılama yapar → daire kararı doğrultusunda karar verir
    │
    └─ DİRENME → önceki kararında ısrar eder (direnme kararı)
                    ↓
              Dosya Genel Kurul'a gider:
              - Hukuk davası → HGK
              - Ceza davası → CGK
                    ↓
              Genel Kurul inceler:
              ├─ Daire'yi haklı bulursa → bozma kesinleşir, mahkeme uymak zorunda
              └─ Mahkeme'yi haklı bulursa → direnme kararı onanır
                    ↓
              HGK / CGK kararı KESİNDİR, taraflar ve mahkeme bağlıdır
```

> **Not:** Direnme yalnızca **ilk bozma** sonrasında mümkündür. HGK/CGK kararından sonra ikinci bir direnme yapılamaz.

#### İçtihadı Birleştirme (Unification of Jurisprudence) — CRITICAL

When different Yargıtay chambers issue conflicting decisions on the same legal question:

```
Different daireler issue conflicting decisions
    ↓
Request by a Daire, Başsavcı, or sufficient members
    ↓
İçtihadı Birleştirme Kurulu convenes:
  - Hukuk İBK → conflicts between civil chambers
  - Ceza İBK → conflicts between criminal chambers
  - İBBGK (Büyük Genel Kurul) → cross-branch conflicts or overarching importance
    ↓
Issues an İçtihadı Birleştirme Kararı (İBK)
    ↓
Published in Resmî Gazete
    ↓
BINDING ON ALL COURTS until changed by new İBK or legislation
```

**Binding effect hierarchy (highest to lowest):**

```
1. İBBGK kararı (İçtihadı Birleştirme Büyük Genel Kurul) — BINDING, treat like statute
2. Hukuk İBK / Ceza İBK — BINDING on all courts
3. HGK / CGK kararları — very high authority (practically binding, formally persuasive)
4. Daire kararları — standard authority (persuasive, not binding)
5. BAM/BİM kararları — lower authority
6. İlk derece kararları — minimal precedential value
```

---

## 3. Danıştay (Council of State — İdari Yargı Yüksek Mahkemesi)

> Per Danıştay Başkanlık Kurulu Kararı No: 2020/62 (modified by 2023/33). **1. Daire** is istişari (advisory) only — NOT a dava dairesi. **11. and 14. Daire** do not exist in the current iş bölümü.

### 3.1 Vergi Dava Daireleri (Tax Litigation)

Vergi dava daireleri: **3., 7. ve 9. Daireler** (per Ortak Hükümler, 2023/33 değişikliği).

| Daire | Topic Keywords |
|-------|---------------|
| **3. Daire** | Gelir vergisi, kurumlar vergisi, KDV — İstanbul BİM temyiz + catch-all vergi |
| **7. Daire** | Gümrük, ithal vergileri, ÖTV, gider vergileri, MTV, veraset ve intikal vergisi |
| **9. Daire** | Gelir/kurumlar/KDV temyiz (İstanbul dışı BİM'ler) + damga, emlak vergisi, belediye vergi/harç |

### 3.2 İdari Dava Daireleri (Administrative Litigation)

Diğer tüm dava daireleri idari dava dairesi olarak görev yapar.

| Daire | Topic Keywords |
|-------|---------------|
| **2. Daire** | İçişleri personeli, emniyet/jandarma, aile hekimliği, kamu görevlileri (5. ve 12. Daire dışı) |
| **4. Daire** | Çevre, kültür/tabiat varlıkları, kıyı, turizm, afet/imar tazminatı, gıda, iş sağlığı |
| **5. Daire** | Hakimler/savcılar (HSK), OHAL KHK işlemleri |
| **6. Daire** | İmar — plan, ruhsat, kamulaştırma, yıkım, para cezaları (4. Daire dışı imar) |
| **8. Daire** | Mahalli idareler, maden/orman, meslek kuruluşları, yükseköğretim, karayolları |
| **10. Daire** | Sağlık/hizmet kusuru, terör zararları, tüketici, yabancı taşınmaz + idari catch-all |
| **12. Daire** | Genel kamu görevlileri (atama, disiplin, parasal haklar), emeklilik, 4/C-4/D |
| **13. Daire** | Rekabet, özelleştirme, kamu ihale (KİK), enerji, telekomünikasyon, RTÜK, sermaye piyasası + idari catch-all |

---

## 4. Sources of Law (Hukuk Kaynakları)

### Hierarchy of Norms (highest to lowest):


| Level | Source                                                  | Notes                                                                                                                                                                   |
| ----- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1     | **Anayasa** (1982 Constitution)                         | Supreme law. Amended many times, most significantly 2017 (presidential system).                                                                                         |
| 2     | **Milletlerarası Andlaşmalar** (International Treaties) | Kanun düzeyinde onaylanır. AY m.90/son (2004): temel hak andlaşmaları kanunlarla çatışırsa andlaşma uygulanır → AİHS pratikte kanunların üstünde. |
| 3     | **Kanun** (Laws)                                        | Enacted by TBMM. Each has a number and name. Published in Resmî Gazete.                                                                                                 |
| 4     | **Cumhurbaşkanlığı Kararnamesi (CBK)**                  | Presidential decrees (post-2018). Cannot regulate fundamental rights or contradict existing laws (AY m.104/17). |
| —     | ~~KHK (Kanun Hükmünde Kararname)~~                      | Decree-laws under the old system. Many OHAL KHKs (2016-2018) remain legally significant.                                                     |
| 5     | **Tüzük** (Bylaws)                                      | Abolished by 2017 constitutional change; no new tüzük can be issued. Existing ones remain in force, rank above yönetmelik.                                |
| 6     | **Yönetmelik** (Regulation)                             | Issued by President, ministries, or public institutions to implement laws.                                                                                              |
| 7     | **Yönerge / Tebliğ / Genelge**                          | Internal administrative guidance. Tebliğ is important in tax and banking.                                                                                               |


### Role of İçtihat (Case Law):

- Turkey is a **civil law** country — case law is NOT formally binding (except İBK)
- **In practice**, Yargıtay/Danıştay decisions carry enormous persuasive weight
- Lower courts that deviate from **yerleşik içtihat** (settled jurisprudence) will almost certainly be reversed on appeal
- A single contrary decision does not overturn yerleşik içtihat — consistency across many decisions is key

---

## 5. Key Codes & Citation Formats

### 5.1 Major Codes


| Abbrev.  | Turkish Name                    | English                  | Law No. | Effective  | Replaced                                                   |
| -------- | ------------------------------- | ------------------------ | ------- | ---------- | ---------------------------------------------------------- |
| **TCK**  | Türk Ceza Kanunu                | Penal Code               | 5237    | 01.06.2005 | 765 s. TCK (1926)                                          |
| **TMK**  | Türk Medenî Kanunu              | Civil Code               | 4721    | 01.01.2002 | 743 s. MK (1926)                                           |
| **TBK**  | Türk Borçlar Kanunu             | Code of Obligations      | 6098    | 01.07.2012 | 818 s. BK (1926)                                           |
| **TTK**  | Türk Ticaret Kanunu             | Commercial Code          | 6102    | 01.07.2012 | 6762 s. TTK (1956)                                         |
| **HMK**  | Hukuk Muhakemeleri Kanunu       | Civil Procedure          | 6100    | 01.10.2011 | 1086 s. HUMK (1927)                                        |
| **CMK**  | Ceza Muhakemesi Kanunu          | Criminal Procedure       | 5271    | 01.06.2005 | 1412 s. CMUK (1929)                                        |
| **İİK**  | İcra ve İflâs Kanunu            | Enforcement & Bankruptcy | 2004    | 1932       | —                                                          |
| **İK**   | İş Kanunu                       | Labor Law                | 4857    | 10.06.2003 | 1475 s. İK (note: m.14 on kıdem tazminatı still in force!) |
| **İYUK** | İdari Yargılama Usulü Kanunu    | Administrative Procedure | 2577    | 1982       | —                                                          |
| **TKHK** | Tüketicinin Korunması Hk. Kanun | Consumer Protection      | 6502    | 2014       | 4077 s. TKHK                                               |


### 5.2 Other Important Laws

- **Kabahatler Kanunu** (Misdemeanors, No. 5326)
- **Arabuluculuk Kanunu** (Mediation, No. 6325)
- **İş Mahkemeleri Kanunu** (Labor Courts, No. 7036)
- **Avukatlık Kanunu** (Attorneys, No. 1136)
- **Noterlik Kanunu** (Notaries, No. 1512)
- **Türk Vatandaşlık Kanunu** (Citizenship, No. 5901)
- **Kaçakçılıkla Mücadele Kanunu** (Anti-Smuggling, No. 5607)
- **VUK** — Vergi Usul Kanunu (Tax Procedure, No. 213)

### 5.3 Article Citation Conventions


| Format            | Example                                           | Meaning                                        |
| ----------------- | ------------------------------------------------- | ---------------------------------------------- |
| Standard short    | **TCK m.302**                                     | TCK Article 302                                |
| With paragraph    | **TCK m.302/1** or **TCK m.302, f.1**             | Article 302, paragraph (fıkra) 1               |
| With subparagraph | **TCK m.53/1-a**                                  | Article 53, paragraph 1, subparagraph (bent) a |
| Long form         | **5237 sayılı Türk Ceza Kanunu'nun 302. maddesi** | Full statutory reference                       |
| Numbered law form | **6098 s. TBK m.49**                              | Law No. 6098, TBK Article 49                   |
| Et seq.           | **TBK m.49 vd.**                                  | Article 49 ve devamı (and following)           |


**Key terms in citations:**

- **m.** = madde (article)
- **f.** = fıkra (paragraph/subsection within an article)
- **b.** = bent (subparagraph, typically lettered a, b, c...)
- **s.** = sayılı (numbered — refers to the law number)
- **vd.** = ve devamı (et seq.)

---

## 6. Case Law Citation Format

### 6.1 Yargıtay Decisions

**Standard format:**

```
Yargıtay [Daire No.] [HD/CD], E. [Yıl]/[Sıra], K. [Yıl]/[Sıra], T. [GG.AA.YYYY]
```


| Component | Meaning                                    | Example       |
| --------- | ------------------------------------------ | ------------- |
| **HD**    | Hukuk Dairesi (Civil Chamber)              | 4. HD         |
| **CD**    | Ceza Dairesi (Criminal Chamber)            | 10. CD        |
| **HGK**   | Hukuk Genel Kurulu                         | HGK           |
| **CGK**   | Ceza Genel Kurulu                          | CGK           |
| **İBK**   | İçtihadı Birleştirme Kararı                | İBBGK         |
| **E.**    | Esas numarası (docket/registration number) | E. 2020/1234  |
| **K.**    | Karar numarası (decision number)           | K. 2020/5678  |
| **T.**    | Tarih (date, DD.MM.YYYY)                   | T. 15.03.2020 |


**Examples:**

```
Yargıtay 4. HD, E. 2020/1234, K. 2020/5678, T. 15.03.2020
Yargıtay HGK, E. 2019/4-345, K. 2020/123, T. 10.06.2020
  → "4-345" means: originating from 4th chamber, case 345
Yargıtay CGK, E. 2018/16-789, K. 2019/456, T. 22.11.2019
  → originating from 16th criminal chamber
Yargıtay İBBGK, E. 2018/1, K. 2019/2, T. 03.03.2019
```

### 6.2 Danıştay Decisions

```
Danıştay [Daire No.] D., E. [Yıl]/[No.], K. [Yıl]/[No.], T. [GG.AA.YYYY]
Danıştay İDDK, E. 2020/100, K. 2020/200, T. 15.09.2020
  → İdari Dava Daireleri Kurulu
Danıştay VDDK, E. 2019/50, K. 2020/75, T. 10.04.2020
  → Vergi Dava Daireleri Kurulu
```

### 6.3 BAM (Bölge Adliye Mahkemesi) Decisions

```
[Şehir] BAM [Daire No.] [HD/CD], E. [Yıl]/[No.], K. [Yıl]/[No.], T. [GG.AA.YYYY]
İstanbul BAM 3. HD, E. 2020/456, K. 2020/789, T. 10.02.2020
Ankara BAM 5. CD, E. 2019/100, K. 2019/200, T. 15.11.2019
```

### 6.4 First Instance Decisions

```
[Şehir] [No.] [Mahkeme Adı], E. [Yıl]/[No.], K. [Yıl]/[No.], T. [GG.AA.YYYY]
İstanbul 3. Asliye Hukuk Mahkemesi, E. 2019/500, K. 2020/300, T. 25.06.2020
Ankara 2. Ağır Ceza Mahkemesi, E. 2018/100, K. 2019/50, T. 14.03.2019
```

### 6.5 Anayasa Mahkemesi Decisions

```
Norm review:      AYM, E. 2019/50, K. 2020/30, T. 15.07.2020
Individual app.:  AYM, Başvuru No: 2018/12345, T. 20.01.2021
Named:            AYM, Mehmet Yılmaz Başvurusu, B. No: 2018/12345, T. 20.01.2021
```

---

## 7. Appeal Flow (İstinaf ve Temyiz)

### 7.1 Civil Cases (Hukuk Davaları)

```
İlk Derece Mahkemesi
    │
    │ İSTİNAF (2 hafta / 2 weeks from gerekçeli karar notification)
    ▼
Bölge Adliye Mahkemesi (BAM) — Hukuk Dairesi
    │
    │ TEMYİZ (2 hafta / 2 weeks from BAM decision notification)
    ▼
Yargıtay — Hukuk Dairesi
```

**Kesinlik sınırı (finality threshold):** Monetary thresholds determine appealability. Updated annually by yeniden değerleme oranı. Some case types are exempt regardless of amount (e.g., taşınmaz davaları, manevi tazminat). Check Resmî Gazete for current values.

### 7.2 Criminal Cases (Ceza Davaları)

```
İlk Derece (Asliye Ceza / Ağır Ceza)
    │
    │ İSTİNAF (2 hafta / 14 days from tebliğ of reasoned judgment)
    ▼
BAM — Ceza Dairesi
    │
    │ TEMYİZ (2 hafta / 14 days from tebliğ)  [*]
    ▼
Yargıtay — Ceza Dairesi
```

> **Süreler:** 7499 s.K. (8. Yargı Paketi, 01.06.2024) ile CMK m.273 ve m.291 değiştirildi — her iki süre de "hükmün gerekçesiyle birlikte tebliğ edildiği tarihten itibaren **2 hafta**" olarak belirlendi.

> **[*] Kesinlik sınırı:** Tüm BAM ceza kararları temyize açık değildir. CMK m.286/2 uyarınca, ilk derece mahkemesince verilen **5 yıl veya daha az** hapis cezasını artırmayan BAM kararları **kesindir** (temyiz edilemez).

### 7.3 Administrative Cases (İdari Davalar)

```
İdare Mahkemesi / Vergi Mahkemesi
    │
    │ İSTİNAF (30 gün / 30 days)
    ▼
Bölge İdare Mahkemesi (BİM)
    │
    │ TEMYİZ (30 gün / 30 days, for eligible cases)
    ▼
Danıştay
```

Many BİM decisions are kesin and cannot go to Danıştay. Danıştay also hears some cases as first instance (e.g., challenges to Cumhurbaşkanlığı kararnameleri, national-scope regulations).

### 7.4 The Bozma → Uyma / Direnme - Israr Flow

This is **unique and critical** to Turkish law:

```
Yargıtay issues BOZMA (reversal)
    │
    ▼
Lower court receives bozma kararı
    │
    ├── UYMA (comply): Lower court follows Yargıtay's reasoning
    │   → Case proceeds per Yargıtay's instructions
    │   → DONE (unless there's a new appeal on the reiterated decision)
    │
    └── DİRENME (resist): Lower court disagrees, reissues same decision
        │
        ▼
    Yargıtay Hukuk Genel Kurulu (HGK) or Ceza Genel Kurulu (CGK)
        │
        ├── HGK/CGK sides with daire → BOZMA is FINAL, lower court MUST comply
        │
        └── HGK/CGK sides with lower court → Decision stands
```

```
Danıştay dairesi issues BOZMA (reversal of BİM kararı)
    │
    ▼
BİM receives bozma kararı
    │
    ├── UYMA (comply): BİM, Danıştay'ın bozma gerekçesine uyar
    │   → DONE
    │
    └── ISRAR (resist): BİM eski kararında ısrar eder (İYUK m.49/4)
        │
        ▼
    Danıştay İdari Dava Daireleri Kurulu (İDDK) veya Vergi Dava Daireleri Kurulu (VDDK)
        │
        ├── Kurul sides with daire → BOZMA is FINAL, BİM MUST comply
        │
        └── Kurul sides with BİM → Decision stands
```

> **Terminoloji farkı:** Adli yargıda "direnme", idari yargıda "ısrar" terimi kullanılır — mekanizma aynıdır.

**Why this matters for retrieval:** Direnme/ısrar kararları sonucu HGK/CGK veya İDDK/VDDK tarafından verilen kararlar, ilgili hukuki mesele için en otoriter içtihat kaynağıdır.

### 7.5 AYM Bireysel Başvuru → AİHM Flow

```
Olağan kanun yolları tükenir (Yargıtay / Danıştay kesin karar)
    │
    │ 30 gün (kesinleşmiş kararın öğrenilmesinden itibaren)
    ▼
Anayasa Mahkemesi — Bireysel Başvuru (AY m.148/3, 6216 s.K. m.45-51)
    │
    ├── Kabul edilemezlik kararı → Kesin
    │
    └── Esastan inceleme
        │
        ├── İHLAL YOK → Kesin
        │
        └── İHLAL VAR
            │
            ├── Yeniden yargılama kararı → Dosya ilk derece mahkemesine döner
            └── Tazminat → Başvurucuya manevi/maddi tazminat
    │
    │ AYM kararının tebliğinden itibaren 4 ay (Protocol 15, 01.02.2022'den itibaren)
    ▼
AİHM (Avrupa İnsan Hakları Mahkemesi) — Strazburg
    │
    ├── Kabul edilemezlik → Kesin
    │
    └── Esastan inceleme
        ├── İhlal yok → Kesin
        └── İhlal var → Tazminat (just satisfaction, AİHS m.41)
                         Türkiye'nin ihlali giderme yükümlülüğü (AİHS m.46)
```

> **Başvuru şartları (AYM):** Anayasa ve AİHS'te **ortak olarak** güvence altına alınan temel hak ve özgürlüklerin kamu gücü tarafından ihlal edildiği iddiası. Olağan kanun yollarının tüketilmesi zorunlu.

> **AİHM'e geçiş:** AYM bireysel başvuru, AİHM'e başvuruda **tüketilmesi gereken iç hukuk yolu** sayılır. AYM'ye başvurmadan doğrudan AİHM'e gidilemez.

---

## 8. Key Legal Terms (Glossary)

### 8.1 Procedural Terms


| Turkish             | English                           | Notes                                                                     |
| ------------------- | --------------------------------- | ------------------------------------------------------------------------- |
| **Dava**            | Lawsuit / case                    |                                                                           |
| **Davacı**          | Plaintiff                         |                                                                           |
| **Davalı**          | Defendant                         |                                                                           |
| **Müdahil**         | Intervenor                        | Ferî müdahil (accessory) or aslî müdahil (principal)                      |
| **Esas**            | Merits / docket number            | Registration number when filed                                            |
| **Karar**           | Decision / judgment number        | Number when decided                                                       |
| **Tensip**          | Preliminary case management order |                                                                           |
| **Ön inceleme**     | Preliminary examination hearing   | Mandatory under HMK; procedural issues + settlement attempt               |
| **Tahkikat**        | Investigation / evidence stage    | Main evidentiary phase                                                    |
| **Sözlü yargılama** | Oral argument stage               | Closing arguments before judgment                                         |
| **Mürafaa**         | Oral hearing (appellate)          |                                                                           |
| **Bilirkişi**       | Court-appointed expert            | Very commonly used; report is NOT binding on judge but highly influential |
| **Islah**           | Amendment of pleadings            | Can only be done once per party (HMK m.176-182)                           |
| **Görev**           | Subject-matter jurisdiction       | Kamu düzeninden — court examines ex officio                               |
| **Yetki**           | Territorial jurisdiction          | Kesin yetki = ex officio; relative yetki = defendant must object          |


### 8.2 Judgment & Appeal Terms


| Turkish             | English                     | Notes                                                                       |
| ------------------- | --------------------------- | --------------------------------------------------------------------------- |
| **Kesinleşme**      | Finalization of judgment    | When decision becomes res judicata (kesin hüküm). Critical for enforcement. |
| **Bozma kararı**    | Reversal decision           | Yargıtay overturns lower court                                              |
| **Onama kararı**    | Affirmation decision        | Yargıtay upholds lower court                                                |
| **Direnme / Israr** | Resistance decision         | Lower court refuses to follow bozma. Adli yargıda "direnme" → HGK/CGK; idari yargıda "ısrar" → İDDK/VDDK. |
| **Karar düzeltme**  | Correction of decision      | **ABOLISHED** with istinaf system (2016). No longer available.              |
| **İstinaf**         | Appeal to BAM/BİM           | Second instance: both factual AND legal review                              |
| **Temyiz**          | Appeal to Yargıtay/Danıştay | Third instance: ONLY legal review (hukukî denetim)                          |
| **Kesinlik sınırı** | Finality threshold          | Below this amount, no further appeal is possible                            |


### 8.3 Substantive Legal Terms


| Turkish                          | English                                | Notes                                                                               |
| -------------------------------- | -------------------------------------- | ----------------------------------------------------------------------------------- |
| **Emsal karar**                  | Precedent / leading case               | Persuasive authority on a legal point                                               |
| **İçtihat**                      | Jurisprudence / case law               | Body of court decisions                                                             |
| **İçtihadı birleştirme**         | Unification of jurisprudence           | Resolves conflicting daire decisions; result is BINDING                             |
| **Yerleşik içtihat**             | Settled jurisprudence                  | Consistent line of decisions; practically binding                                   |
| **Hukukî mütalaa**               | Legal opinion / memorandum             | Expert academic opinion submitted to courts (HMK m.293)                             |
| **Zamanaşımı**                   | Statute of limitations                 | Debtor must raise as defense in civil; ex officio in criminal                       |
| **Hak düşürücü süre**            | Preclusive / forfeiture period         | Court applies ex officio; right is completely extinguished                          |
| **Yürütmenin durdurulması (YD)** | Stay of execution                      | Admin court suspends an act. Requires: açıkça hukuka aykırılık + telafisi güç zarar |
| **Arabuluculuk**                 | Mediation                              | Mandatory pre-suit for: iş (2018), ticari (2019), tüketici (2020, 7251 s.K.) disputes |
| **Uzlaştırma**                   | Criminal conciliation                  | ADR for certain offenses (CMK m.253-255)                                            |
| **Suç duyurusu**                 | Criminal complaint                     |                                                                                     |
| **İddianame**                    | Indictment                             |                                                                                     |
| **Beraat**                       | Acquittal                              |                                                                                     |
| **Mahkûmiyet**                   | Conviction                             |                                                                                     |
| **Tutuklama**                    | Pre-trial detention                    |                                                                                     |
| **KYOK**                         | Kovuşturmaya yer olmadığına dair karar | Decision not to prosecute                                                           |


### 8.4 Frequently Referenced Article Ranges


| Topic                                    | Code & Articles                                                                                                                                                                        |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tort/delict (haksız fiil)                | TBK m.49-76                                                                                                                                                                            |
| Contract breach                          | TBK m.112-126                                                                                                                                                                          |
| Unjust enrichment (sebepsiz zenginleşme) | TBK m.77-82                                                                                                                                                                            |
| General statute of limitations           | TBK m.146 (10 years), m.147 (5 years for specific claims)                                                                                                                              |
| Divorce grounds                          | TMK m.161 (zina), m.162 (hayata kast/pek kötü muamele), m.163 (suç işleme/haysiyetsiz hayat sürme), m.164 (terk), m.165 (akıl hastalığı), m.166 (evlilik birliğinin temelinden sarsılması / anlaşmalı boşanma) |
| Severance pay (kıdem tazminatı)          | **1475 s. İK m.14** (STILL IN FORCE from old labor law!)                                                                                                                               |
| Wrongful termination (işe iade)          | 4857 s. İK m.18-21                                                                                                                                                                     |
| Intentional homicide                     | TCK m.81 (kasten), m.82 (nitelikli), m.85 (taksirle)                                                                                                                                   |
| Theft                                    | TCK m.141-147                                                                                                                                                                          |
| Fraud (dolandırıcılık)                   | TCK m.157-159                                                                                                                                                                          |
| Sexual offenses                          | TCK m.102-105                                                                                                                                                                          |
| Drug offenses                            | TCK m.188 (imal/ticaret), m.191 (kullanım)                                                                                                                                             |
| Forgery                                  | TCK m.204-206                                                                                                                                                                          |
| Abuse of office                          | TCK m.257                                                                                                                                                                              |


---

## 9. Structure of a Turkish Court Decision

### 9.1 First Instance Court Decision

#### Hukuk (Civil) Kararı

| Section       | Turkish          | Content                                                                                                                                                                                  |
| ------------- | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Header        | Başlık           | Court name, Esas No., Karar No., judge name(s), clerk                                                                                                                                    |
| Parties       | Taraflar         | Davacı (plaintiff), Davalı (defendant), TC Kimlik No., attorneys                                                                                                                         |
| Subject       | Dava konusu      | Type of case (e.g., "alacak davası", "boşanma davası")                                                                                                                                   |
| Dates         | Tarihler         | Filing date (dava tarihi), decision date (karar tarihi)                                                                                                                                  |
| Claims        | İddia            | Plaintiff's factual allegations and legal arguments                                                                                                                                      |
| Defense       | Savunma          | Defendant's response                                                                                                                                                                     |
| Evidence      | Deliller         | List of evidence examined                                                                                                                                                                |
| Expert report | Bilirkişi raporu | If applicable                                                                                                                                                                            |
| Reasoning     | Gerekçe          | Court's legal analysis. Citations to applicable laws and içtihat. **This is where the legal principles are.**                                                                            |
| Disposition   | Hüküm            | The actual order: kabul (accepted), ret (rejected), kısmen kabul (partially accepted). Plus yargılama giderleri (costs), vekâlet ücreti (attorney fee), and whether appeal is available. |

#### Ceza (Criminal) Kararı — farklılıklar

| Section       | Turkish                      | Content                                                                                                          |
| ------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Parties       | Sanık / Katılan / Müşteki    | Sanık (accused), Katılan (intervening party), Müşteki (complainant) — Savcılık (prosecution) ayrıca yer alır     |
| Subject       | Suç / Sevk maddeleri         | İsnat edilen suç ve ilgili TCK maddeleri                                                                         |
| Claims        | İddianame özeti              | Savcılığın iddianamesi özetlenir                                                                                  |
| Prosecution   | Esas hakkında mütalaa        | Savcılığın son duruşmadaki görüşü                                                                                |
| Defense       | Sanığın savunması            | Sanık ve müdafiinin savunması                                                                                     |
| Disposition   | Hüküm                       | **Beraat**, **mahkûmiyet** (ceza miktarı + infaz rejimi), **düşme**, **ceza verilmesine yer olmadığı (CYPO)**, **HAGB** |


### 9.2 BAM / BİM İstinaf Kararı

#### BAM (Adli Yargı İstinaf)

| Section           | Content                                                                                                                                 |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Header            | BAM Hukuk/Ceza Dairesi, E., K. numbers                                                                                                  |
| Lower court       | İncelenen ilk derece mahkemesi kararı                                                                                                    |
| Lower decision    | İlk derece kararının özeti                                                                                                               |
| Appellant         | İstinaf eden taraf                                                                                                                       |
| Grounds of appeal | İstinaf sebepleri                                                                                                                        |
| Analysis          | İnceleme ve Gerekçe — hem maddi olay hem hukuki denetim (re-examination of facts + law)                                                 |
| Conclusion        | **Esastan ret** (istinafın reddi — ilk derece kararı aynen), **kaldırma** (ilk derece kararı kaldırılıp yeniden yargılama için geri gönderme), **düzelterek/değiştirerek karar** (BAM bizzat yeni karar verir), **davanın kabulü/reddi** (BAM esastan yeniden karar verir). Oy birliği / oy çoğunluğu belirtilir. |
| Dissent           | Karşı oy (if any)                                                                                                                        |

#### BİM (İdari Yargı İstinaf)

| Section           | Content                                                                                                                                 |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Header            | BİM İdari/Vergi Dava Dairesi, E., K. numbers                                                                                            |
| Lower court       | İncelenen İdare/Vergi Mahkemesi kararı                                                                                                   |
| Conclusion        | **İstinaf başvurusunun reddi** (ilk derece kararı aynen), **kaldırma + yeniden yargılama için geri gönderme**, **kaldırma + BİM'in bizzat karar vermesi**. Oy birliği / oy çoğunluğu belirtilir. |

### 9.3 Yargıtay Decision


| Section           | Content                                                                                                                                 |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Header            | Daire, E., K. numbers                                                                                                                   |
| Parties           | Often abbreviated                                                                                                                       |
| Lower court       | İncelenen BAM kararı (üç kademeli sistemde Yargıtay, BAM kararını inceler)                                                              |
| Lower decision    | Summary of the BAM decision under review                                                                                                |
| Appellant         | Temyiz eden (who appealed)                                                                                                              |
| Grounds of appeal | Temyiz sebepleri                                                                                                                        |
| Analysis          | İnceleme ve Gerekçe — Yargıtay's legal analysis                                                                                         |
| Conclusion        | **BOZMA** (reversal), **ONAMA** (affirmance), **düzelterek onama** (affirmance with correction), **kısmen bozma** (partial reversal). States oy birliği (unanimous) or oy çoğunluğu (majority). |
| Dissent           | Karşı oy / muhalefet şerhi (if any)                                                                                                     |


### 9.4 Danıştay Decision


| Section           | Content                                                                                                                                 |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Header            | Daire, E., K. numbers                                                                                                                   |
| Parties           | Davacı, Davalı idare (+ varsa davalı yanında müdahil)                                                                                   |
| Lower court       | İncelenen BİM kararı (temyiz) veya ilk derece mahkemesi kararı (Danıştay'ın ilk derece olarak baktığı davalarda yoktur)                 |
| İstemin özeti     | Temyiz/dava talebinin özeti                                                                                                              |
| Savunmanın özeti  | Karşı tarafın (idarenin) savunması                                                                                                       |
| Danıştay Savcısı  | Danıştay Savcısının düşüncesi (tüm davalarda yer almaz)                                                                                 |
| Analysis          | İnceleme ve Gerekçe — Danıştay'ın hukuki değerlendirmesi                                                                                |
| Conclusion        | **BOZMA**, **ONAMA**, **düzelterek onama**, **kısmen bozma**; ilk derece sıfatıyla: **iptal**, **ret**, **kısmen iptal**. Oy birliği / oy çoğunluğu belirtilir. |
| Dissent           | Karşı oy / muhalefet şerhi (if any)                                                                                                     |


### 9.5 AYM Bireysel Başvuru Kararı


| Section              | Content                                                                                                                                 |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Header               | Başvuru Numarası (e.g., 2019/12345), Karar Tarihi                                                                                       |
| Başvurucu            | Başvurucunun adı/soyadı (veya tüzel kişi unvanı), avukatı                                                                               |
| Başvuru konusu       | Hangi temel hakkın ihlal edildiği iddiası (e.g., adil yargılanma hakkı, mülkiyet hakkı, ifade özgürlüğü)                                |
| Olaylar              | Olayların özeti — ilk derece, istinaf, temyiz sürecinin kronolojik anlatımı                                                             |
| İlgili hukuk         | Uygulanacak Anayasa maddeleri, AİHS maddeleri, ilgili kanun hükümleri, AİHM içtihadı                                                    |
| Kabul edilebilirlik  | Başvuru şartlarının karşılanıp karşılanmadığı (süre, kanun yollarının tüketilmesi, açıkça dayanaktan yoksunluk vb.)                     |
| Esas inceleme        | AYM'nin hak ihlali değerlendirmesi — ölçülülük, meşru amaç, demokratik toplumda gereklilik testleri                                      |
| Conclusion           | **Kabul edilemezlik**, **ihlal olmadığına karar**, **ihlal kararı** + hukuki sonuç: **yeniden yargılama** ve/veya **tazminat** (manevi/maddi). Oy birliği / oy çoğunluğu belirtilir. |
| Dissent              | Karşı oy (if any) — AYM kararlarında sıklıkla detaylı karşı oylar yer alır                                                             |


### 9.6 AİHM Kararı

> **Note:** AİHM (ECHR) decisions are written in English and/or French. Unofficial Turkish translations are published by the Ministry of Justice. If the corpus includes AİHM decisions, add a full decision structure template here. Key sections: Header (Başvuru No.), Parties (X v. Turkey), Procedure, Facts, Relevant law, Admissibility, Merits (AİHS article-by-article review: müdahale, meşru amaç, ölçülülük), Conclusion (ihlal var/yok + just satisfaction under AİHS m.41), Dissent/Concurring opinions.

---

## 10. Legal Databases & Research Sources


| Database                                | Type               | Access | Notes                                                                              |
| --------------------------------------- | ------------------ | ------ | ---------------------------------------------------------------------------------- |
| **Kazancı** (kazanci.com.tr)            | Comprehensive      | Paid   | Oldest, most complete. Yargıtay, Danıştay, BAM, BİM, AYM, legislation, literature. |
| **Lexpera** (lexpera.com.tr)            | Comprehensive      | Paid   | Strong on legislation + case law. Run by Seçkin Yayıncılık.                        |
| **Sinerji** (sinerjimevzuat.com.tr)     | Comprehensive      | Paid   | Legislation, case law, practice tools.                                             |
| **UYAP** (uyap.gov.tr)                  | Judiciary system   | Auth   | National electronic filing. Lawyer portal for case tracking.                       |
| **emsal.yargitay.gov.tr**               | Yargıtay decisions | Free   | Official but limited search.                                                       |
| **emsal.danistay.gov.tr**               | Danıştay decisions | Free   | Official Danıştay search.                                                          |
| **kararlarbilgibankasi.anayasa.gov.tr** | AYM decisions      | Free   | Constitutional Court decision bank.                                                |
| **mevzuat.gov.tr**                      | Legislation        | Free   | Official legislation database (Presidency). Current text of all laws.              |
| **resmigazete.gov.tr**                  | Official Gazette   | Free   | All laws, regulations, İBK published here.                                         |
| **kararara.com**                        | Case law           | Free   | Community legal database with Yargıtay decisions.                                  |


### How Turkish Lawyers Research

1. Check **mevzuat.gov.tr** for current law text
2. Search **Kazancı/Lexpera** for Yargıtay/Danıştay decisions using keywords, article numbers, or legal concepts
3. Check for **İBK** on the specific legal point (binding — overrides regular içtihat)
4. Check **HGK/CGK** decisions (carry more weight than daire decisions)
5. Look for **yerleşik içtihat** (consistent line of decisions)
6. Check academic **şerh** (article-by-article commentaries) for major codes
7. Check **Resmî Gazete** for recent legislative amendments

---

## 11. Critical Rules for AI/RAG Systems Working with Turkish Law

### 11.1 Decision Authority for Retrieval Ranking

A retrieval system MUST rank results considering this hierarchy:

#### Adli Yargı (Yargıtay)

| Weight        | Source                                                                      | Binding?                                 |
| ------------- | --------------------------------------------------------------------------- | ---------------------------------------- |
| **Highest**   | İçtihadı Birleştirme Kararları (İBK)                                        | YES — legally binding on all courts      |
| **Very High** | Yargıtay HGK / CGK kararları                                                | Practically binding (arise from direnme) |
| **High**      | Yargıtay Daire kararları (yerleşik içtihat — multiple consistent decisions) | Persuasive but strong                    |
| **Medium**    | Single Yargıtay Daire kararı                                                | Persuasive                               |
| **Lower**     | BAM kararları                                                               | Useful for trends, lower authority       |
| **Lowest**    | İlk derece kararları                                                        | Minimal precedential value               |

#### İdari Yargı (Danıştay)

| Weight        | Source                                                                      | Binding?                                 |
| ------------- | --------------------------------------------------------------------------- | ---------------------------------------- |
| **Highest**   | Danıştay İçtihadı Birleştirme Kararları (İBK)                               | YES — legally binding on all idari courts |
| **Very High** | İDDK / VDDK kararları                                                        | Practically binding (arise from ısrar)   |
| **High**      | Danıştay Daire kararları (yerleşik içtihat)                                  | Persuasive but strong                    |
| **Medium**    | Single Danıştay Daire kararı                                                 | Persuasive                               |
| **Lower**     | BİM kararları                                                                | Useful for trends, lower authority       |
| **Lowest**    | İdare / Vergi Mahkemesi kararları                                            | Minimal precedential value               |

#### Anayasa Yargısı & Uluslararası

| Weight        | Source                                                                      | Binding?                                 |
| ------------- | --------------------------------------------------------------------------- | ---------------------------------------- |
| **Very High** | AYM bireysel başvuru kararları                                               | Temel hak ihlali konularında çok otoriter; yeniden yargılama sebebi |
| **Very High** | AYM iptal kararları (norm denetimi)                                          | YES — erga omnes, Resmî Gazete'de yayımlanır |
| **High**      | AİHM kararları (Türkiye aleyhine)                                            | AY m.90 gereği güçlü etki; yeniden yargılama sebebi (CMK m.311/1-f) |
| **Medium**    | AİHM kararları (diğer devletler aleyhine)                                    | Aynı AİHS maddesi yorumlandığında persuasive |


### 11.2 Critical Temporal Boundaries

Cases citing OLD codes may be irrelevant or require careful mapping:


| Transition                             | Date       | Impact                                                                                                       |
| -------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------ |
| Old TCK (765) → New TCK (5237)         | 01.06.2005 | Different article numbers, different legal frameworks. "Lehe kanun" principle means courts may compare both. |
| Old MK (743) → New TMK (4721)          | 01.01.2002 | Property, family, inheritance law restructured.                                                              |
| Old BK (818) → New TBK (6098)          | 01.07.2012 | Article numbers changed entirely.                                                                            |
| Old TTK (6762) → New TTK (6102)        | 01.07.2012 | Major commercial law modernization.                                                                          |
| Old HUMK (1086) → New HMK (6100)       | 01.10.2011 | Significant procedural changes.                                                                              |
| Old CMUK (1412) → New CMK (5271)       | 01.06.2005 | Criminal procedure overhaul.                                                                                 |
| No İstinaf → İstinaf (BAM operational) | 20.07.2016 | Before: first instance → Yargıtay directly. After: first instance → BAM → Yargıtay.                          |


**RAG implication:** A 2003 Yargıtay decision on 765 s. TCK m.448 (old homicide article) may NOT be relevant to a query about 5237 s. TCK m.81 (new homicide article) unless the legal principle transcends the code change. Always check which code version a decision applies.

### 11.3 Conflicting Chamber Decisions

- Different daireler of Yargıtay **regularly** reach conflicting conclusions
- Example: one daire holds zamanaşımı is 5 years for a contract type; another holds 10 years
- Until İBK resolves the conflict, BOTH lines coexist
- **Retrieval system must:**
  1. Identify which daire issued each decision
  2. Check if an İBK exists on the specific point
  3. Check for HGK/CGK decisions
  4. **If no İBK/HGK resolution exists: surface both conflicting lines to the user**
  5. Indicate which line has more recent and more numerous supporting decisions

### 11.4 Yerleşik İçtihat (Settled Jurisprudence)

- When Yargıtay (or a specific daire) consistently decides a question the same way across **multiple** decisions over time → yerleşik içtihat
- Multiple consistent decisions pointing the same direction are stronger than a single decision, even a more recent one
- For retrieval: cluster decisions by legal issue and identify consistency patterns

### 11.5 Document Processing Considerations

1. **Anonymization:** Turkish court decisions increasingly anonymize party names (initials). Affects name-based search.
2. **UYAP metadata:** Standardized: mahkeme, esas no, karar no, tarih. Reliable for indexing.
3. **Gerekçe vs Hüküm:** For legal principle extraction, the **gerekçe** (reasoning) is primary. The **hüküm** (disposition) is the order.
4. **Karşı oy (dissent):** Can signal future direction of law. Flag but don't treat as the holding.
5. **Bilirkişi reports:** Referenced in decisions but are expert opinions, not legal holdings.
6. **AİHM (ECHR) decisions:** Relevant per Anayasa m.90. Key areas: ifade özgürlüğü (expression), adil yargılanma hakkı (fair trial), tutukluluk (detention).
7. **1475 s. İK m.14:** Kıdem tazminatı (severance pay) is STILL governed by the OLD labor law's Article 14, even though the rest of 1475 was replaced by 4857. This single article is one of the most frequently litigated provisions in Turkish law.

### 11.6 Regex Patterns for Citation Extraction

Common patterns in Turkish legal text:

```
Yargıtay citation:
  Yargıtay\s+(\d+)\.\s*(HD|CD|HGK|CGK)\s*,?\s*E\.\s*(\d{4})/(\d+)\s*,?\s*K\.\s*(\d{4})/(\d+)\s*,?\s*T\.\s*(\d{2}\.\d{2}\.\d{4})

Danıştay citation:
  Danıştay\s+(\d+)\.\s*D\.\s*,?\s*E\.\s*(\d{4})/(\d+)\s*,?\s*K\.\s*(\d{4})/(\d+)

Article reference:
  (TCK|TMK|TBK|TTK|HMK|CMK|İİK|İK|İYUK|TKHK)\s*m\.?\s*(\d+)(/(\d+))?
  (\d{3,5})\s*sayılı\s*(Kanun|.*Kanunu?).*?m\.?\s*(\d+)

BAM citation:
  (İstanbul|Ankara|İzmir|...)\s*BAM\s*(\d+)\.\s*(HD|CD)\s*,?\s*E\.\s*(\d{4})/(\d+)
```

### 11.7 Mandatory Mediation (Arabuluculuk) Requirements

Cases where mediation is a **dava şartı** (precondition for filing):


| Domain                                | Since      | Law                                |
| ------------------------------------- | ---------- | ---------------------------------- |
| İş (labor) disputes                   | 01.01.2018 | 7036 s. İş Mahkemeleri Kanunu m.3  |
| Ticari (commercial) disputes          | 01.01.2019 | 6102 s. TTK m.5/A                  |
| Tüketici (consumer) disputes          | 28.07.2020 | 7251 s.K. → 6502 s. TKHK m.73/A   |
| Kira (lease) disputes — certain types | 01.09.2023 | 6325 s. Arabuluculuk Kanunu ek m.2 |


**Implication for case law:** Post-mediation-mandate decisions may reference the arabuluculuk son tutanağı (final mediation report). Pre-mandate decisions for the same type of dispute followed a different procedural path.
