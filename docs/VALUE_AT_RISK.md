# Value at risk: research and recommended formulation

Research note, 2026-09-19. Three surveys were run in parallel: the codebase (where a value-at-risk
layer would plug in and what data it has), the wildfire risk-assessment literature (eNVC, CVaR,
damage functions, evacuation casualty evidence), and Spanish/Catalan valuation inputs and legal
framing. Figures marked **[est.]** are derivations or unverified; everything else is sourced in
the citation list. Nothing in the code was changed.

## 1. Recommendation in one paragraph

Keep three separate ledgers and never merge them into one number: **people** (counts, the
headline), **property** (euro replacement value at risk), and **land** (hectares with a euro
band). Compute each as an expected value under the spread ensemble using the standard USFS
expected-net-value-change framework collapsed to an intensity-blind damage ratio, because the
forecast provides burn probability and arrival time but no fire intensity. Show ranges, not
points. Contact order stays by remaining evacuation window (readme section 6); euros are a
secondary column and a scenario header aggregate, never a sort key. Monetised lives (value of a
statistical life) belong only in a separate planning / after-action cost-benefit view, with a
sensitivity range, and never appear next to a live ranking.

## 2. The framework and why it fits

Scott, Thompson & Calkin (2013, USFS RMRS-GTR-315), building on Finney (2005), define wildfire
risk for one highly valued resource or asset (HVRA) as expected net value change:

    E(NVC)  = Σ_i BP_i × NVC_i                 (sum over fire-intensity levels i)
    cNVC    = Σ_i FLP_i × RF_i                 (conditional loss if fire arrives; FLP = flame-length
                                                probabilities, RF = response function on -100..+100)
    eNVC    = BP × cNVC

The USFS Wildfire Risk to Communities tool, WFDSS FSPro + RAVAR (the US incident-time tool,
Calkin et al. 2011), and the JRC pan-European wildfire risk assessment (Oom et al. 2022) all use
this shape. Two things GTR-315 says matter here: it rejects monetary valuation for most non-market
HVRAs and uses relative-importance weights instead, and it notes risk can also be expressed as
"a 15 percent chance of losses exceeding a threshold", which is the door to VaR/CVaR.

**With burn probability and arrival time only** (this project), the intensity sum collapses to a
class constant. Replace Σ_i FLP_i RF_i by a damage ratio d_c in [0, 1] per asset class and label
it "intensity-blind". BP must be the incident-conditional probability that the fire reaches the
cell within horizon H (fraction of ensemble members), which is what `burn_probability` already is
(`spread.py:172-186`, `forecast_input.py:220-241`). Upgrade paths: ask Deepfire to expose
ELMFIRE's fireline-intensity raster, or approximate intensity from the arrival-time gradient.

## 3. Per-asset formulas

Inputs per asset a: class c, occupancy occ_a, evacuation duration t_evac,c (from
`EVACUATION_POLICY`), replacement value V_a, damage ratio band d_c, warning latency t_warn,
horizon H. Fire inputs: `burn_probability` and arrival p10/p50/p90.

    P_reach_a(H)        = burn_probability_a                          (point)
    slack_q             = arrival_q - (now + t_warn + t_evac,c)        q in {p10, p50, p90}
    people_exposed_a    = occ_a × P_reach_a                            (point)
    people_at_risk_a    = occ_a × P(slack < 0)                         (fraction of members / quantiles
                                                                        with negative slack)
    expected_loss_a     = P_reach_a × d_c × V_a                        (report [low, mid, high]
                                                                        from d_c and V_a bands)
    E[fatalities_a]     = occ_a × P_reach_a × P_notevac(slack) × CFR_c  (optional, collapsed by
                                                                        default, labelled)

Rules carried over from the project contract: unknown occupancy or value is `null`, never zero
(CONTRACTS.md:47, PLAN.md:190); `capacity` is never used as a headcount (`app.people()`,
`CapacityAsOccupancyError`), so people_exposed uses `estimated_occupancy` only and capacity may
appear only as a labelled upper bound; every computed field gets a `sources` entry.

Damage ratio evidence for P(destroyed | fire reaches):

| Setting | Loss ratio | Source |
|---|---|---|
| California 1985–2013, 89 fires, buildings inside perimeters | 14% (interface 15.6%, intermix 11.6%) | Kramer et al. 2019 |
| 2018 Camp Fire, Paradise | 79.7% of residential structures | Knapp et al. 2021 |
| Black Saturday 2009, homes within 10 m of bush | 80–90% | McAneney/Chen 2009 |
| AFDRS impact index, per-house logistic model | 0.07 to 0.91 by configuration | IJWF 2025 |
| CAL FIRE DINS damage classes | none / 1–9% / 10–25% / 26–50% / 51–100% | CAL FIRE |

Suggested bands **[est.]**: d_c low/mid/high = 0.15 / 0.40 / 0.80; lower for confinable masonry
classes (hospital, care home), higher for campsites (tents, caravans) and masies in forest.

## 4. Casualties and evacuation timing

No published casualty-rate curve versus warning time exists for wildfire. The consistent finding
across Australia (Haynes et al. 2010, 2020; Blanchi et al. 2014), Southern Europe (Molina-Terrén
et al. 2019; Diakakis et al. 2016) and the case studies is that civilians die in **late
self-evacuation by road** and **outdoor entrapment**, rarely in confined buildings:

- Pedrógão Grande 2017: 66 dead, 47 on one 400 m stretch of the N236-1 (30 in cars).
- Mati 2018: 102–104 dead in under 3 h, no evacuation ordered; evacuation safety factor
  (available / required time, Kalogeropoulos et al. 2025) was below 1 from detection.
- Torrefeta 2025 (Segarra): 2 farmers trapped in a vehicle while 20,000 people were confined
  in 11 municipalities; Spain 2025 about 2 deaths per 100,000 ha burned.
- Los Gallardos (Almería), July 2026: at least 12 dead, found in vehicles and a riverbed escape route.

The slack the project already computes is exactly the quantity these studies use (Cova et al.
2005 trigger buffers, WUIVAC, Li, Cova & Dennison 2019). Rough calibration **[est.]**:
P_notevac ≈ 1 when slack < 0, residual 0.05–0.15 when ample; CFR ≈ 0.02–0.05 caught outdoors or
on the road, 0.001–0.005 confined in a defensible building, × 3–5 for non-ambulatory classes.
CFR is the weakest number in the chain: the headline should be "people not cleared in time",
with fatalities as a collapsed range.

## 5. Scenario aggregates and tail measures

    loss_k  = Σ_a 1[burned_k(a)] × d_c × V_a        for ensemble member k = 1..N
    EL      = (1/N) Σ_k loss_k = Σ_a P_reach_a × d_c × V_a
    VaR_α   = empirical α-quantile of {loss_k}
    CVaR_α  = mean of the ⌈(1-α)N⌉ largest loss_k    (Rockafellar & Uryasev 2000/2002)

EL needs only per-asset burn probability. VaR/CVaR need member-level burned masks (the joint
distribution across assets). Deepfire returns probability bands in multiples of 1/N, not member
masks (`forecast_input.py:250-302`), so a true CVaR is not computable from the API today. Bound it
instead: "everything burns at its p10 arrival" is a pessimistic upper bound, p90 an optimistic
one; label these as bounds, not quantiles. The in-house CA does keep members (`spread.py`), so
CVaR is computable there. With N ≈ 10, CVaR_0.9 is just the worst member; use α = 0.8 or report max.

Which measure when: expected value is the primary incident number (additive, robust with few
members); CVaR or the p10 bound is the upper end of a range. VaR alone is inappropriate (ignores
the tail, jumps between members). Annual average loss and exceedance curves are planning tools
over many ignitions, not incident tools. Note for the UI: CVaR over a spread ensemble is standard
finance methodology applied by this project, not a wildfire-literature convention (no
peer-reviewed wildfire CVaR-for-suppression paper was found).

## 6. Valuation inputs for Catalonia

### Property replacement value V_a

Use the Agència Tributària de Catalunya "Valors bàsics dels immobles urbans 2025", which values
construction explicitly by replacement (base module 1,000 EUR/m² built × typology × quality):

| ATC 2025 class | very modest – average – very good (EUR/m² built) |
|---|---|
| Docent (schools) | 600 – 1,000 – 1,500 |
| Sanitari (hospitals, clinics, care homes) | 780 – 1,300 – 1,950 |
| Detached single-family | 750 – 1,250 – 1,875 |
| Rural housing (masies) | 540 – 900 – 1,485 |
| Hotels (proxy for campsite and camp buildings) | 1,148 – 1,620 – 2,498 |
| Multi-family housing (nuclei) | 600 – 1,000 – 1,500 |

Cross-checks: recent hospital tenders 2,200–2,400 EUR/m² construction only; Infraestructures.cat
schools ≈ 1,300–1,700 EUR/m² **[est.]**; care homes ≈ 65,000 EUR/bed. Practical rule **[est.]**:
V_a = built area × ATC value × 1.3–1.5 (fees, VAT, contents); hospitals +30–40% for equipment.
Index with the Ministerio de Transportes construction cost index.

Built area: the snapshot has no footprints (`area_m2` null for all 168 Gavarres assets). The
Catastro INSPIRE Buildings WFS (`https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx`, typename
BU.BUILDING, bbox in EPSG:25831) gives `currentUse`, `numberOfFloorsAboveGround`,
`dateOfConstruction` and gross built area per building, free and without a key; ATOM
per-municipality GML dumps allow preloading in `scripts/fetch_data.py`. Cadastral *value* is
protected and is about half of market value at a decades-old revision date, so it is unusable.
Fallback when area is unknown: per-class default with `value_basis = "assumed"` and a review
reason, or `null`; never zero.

Insurance angle: wildfire is not an extraordinary risk under RD 300/2004, so the Consorcio de
Compensación de Seguros does not cover property loss; it falls on private policies. The figure
computed here is total economic loss, insured plus uninsured, not an insurer's number.

### Land

| Item | Value | Source |
|---|---|---|
| Suppression, large fires | 10,000–19,000 EUR/ha | Forescat via press |
| Post-fire restoration | ≥ 2,500 EUR/ha | COITF |
| Catalonia forest ecosystem services | ≈ 2,225 M EUR/yr over 2.08 M ha ≈ 1,070 EUR/ha/yr **[est.]** | PGPF 2036 |
| EU average all-in wildfire loss | ≈ 4,000–6,000 EUR/ha **[est.]** | JRC/EFFIS |
| Suggested ResponsAra defaults **[est.]** | forest 5,000–8,000; cropland 1,000–1,500; pasture 400 EUR/ha | |

### Lives (planning view only)

| Source | Figure |
|---|---|
| DGT 2024 update (Abellán et al., Univ. Murcia) | value of preventing a fatality **2.0 M EUR** (VSL 1.9 M), ~2023 prices |
| DGT 2011 legacy | 1.4 M EUR |
| EC Better Regulation / EUROCONTROL 2025 prices | 4.77 M EUR |
| OECD 2025 EU base, scaled to Spain | ≈ 6.5–7 M EUR **[est.]** |
| Serious / slight injury | ≈ 13% / 1% of VSL **[est.]** |

Recommended: central 2.0 M EUR, range 1.4–4.8 M EUR. The Ley 35/2015 baremo (about 114 k EUR
per spouse or young child in 2025) is ex-post tort compensation, not a VSL, and must not be used
here. QALY-based values (≈ 0.75 M EUR) reflect health-budget opportunity cost, not risk
preferences; disaster cost-benefit uses VSL.

Service disruption and evacuation cost: EU/JRC and Sendai practice report hospital and school
disruption as **counts** (facility-days, people served), not euros. Per-evacuee shelter cost proxy
≈ 103 EUR/person/day hotel (RD 462/2002 per-diem), 20–40 EUR pavilion **[est.]**.

## 7. Legal and ethical framing

- Ley 17/2015 and INFOCAT (revised 2024, updated 2026) order priorities persons, then goods,
  then environment. Confinement is the default; evacuation only under imminent grave danger to
  life when confinement is unsafe, weighing road capacity, smoke, reduced-mobility groups and
  "time available before the fire arrives". No monetary criterion appears in the doctrine.
- OECD 2025 warns against adjusting VSL by age or risk type; a live ranking by euros of lives
  would do that implicitly (children in schools versus elderly in care homes).
- EU AI Act Annex III 5(d) lists systems that establish priority in dispatching emergency first
  response as high-risk **[interpretation]**: human oversight, transparency, logging of inputs.
- UI consequences: people exposed by vulnerability class and time margin is the headline; euros
  only for property and land, labelled "estimate (ATC 2025 replacement cost, ±30%)"; no "EUR per
  life" anywhere; no sort or filter by money; confine-versus-evacuate shown as information for the
  INFOCAT director, not a recommendation; every input behind a ranking logged.

## 8. Where it plugs into the code

- Flag: add `"value_at_risk": False` to `config.FEATURES` (`config.py:15-20`), enabled only via a
  per-call `cfg` shim like the other flags (`scripts/make_snapshots.py:598-602`).
- Policy: a versioned `VALUE_AT_RISK_POLICY` next to `VALUE_POLICY` (`config.py:27-38`) holding
  d_c bands, ATC EUR/m² by class, multipliers, t_warn, and the land defaults. The existing
  `value_score` (0.4–1.0 operational importance, "not a monetary valuation") stays as it is.
- Fields: `replacement_value_eur`, `replacement_value_basis`, `expected_loss_eur_low/mid/high`,
  `people_exposed`, `people_at_risk`, computed in `snapshot.asset_record`
  (`snapshot.py:329-357`), added to `ASSET_KEYS` (`snapshot.py:56-61`), `_COMPUTED_FIELDS`
  (`snapshot.py:64-67`) and `validate_snapshot`, each with a `sources` entry. Re-derive on
  `asset_type` override like `value_score` (`priority.py:156-167`).
- Data: a Catastro fetch in `scripts/fetch_data.py` producing built area and use per asset;
  land cover per burned cell for the hectare ledger.
- Aggregates: `ui_state.Session.status()` counts (`ui_state.py:276-291`) feeding the metrics
  row (`app.py:430-436`): people exposed, people at risk, assets with p10 slack < 0, expected
  loss mid with band, upper bound (p10 scenario or CVaR where members exist).
- Per-asset columns: `app.ranked_frame` / `review_frame` (`app.py:153-174`), never as sort keys;
  detail in `components_frame`.
- Response planner: `priority_models.Location.value` (`priority_models.py:16-18`) can carry
  `replacement_value_eur`; the lexicographic objective assisted > people > value
  (`response_priority.py:158-163`) already matches the INFOCAT ordering.
- Data gaps today: `burn_probability` populated for 67 of 168 assets in the Gavarres 0002
  snapshot and none in 0004; 12 assets `occupancy_unknown`; 69 unlocated; no footprints. The layer
  must return `null` with a review reason for all of these.

## 9. What to label as assumption

d_c (intensity-blind), V_a per m² and the multiplier, occupancy defaults, t_warn, P_notevac
shape, CFR, comonotonic bounds when member masks are absent, land EUR/ha, and the CVaR step
itself.

## Citations

Framework and tools
- Finney 2005, The challenge of quantitative risk analysis for wildland fire, For. Ecol. Manage. 211 — https://research.fs.usda.gov/sites/default/files/2024-01/firelab-finney_quantitative_risk_analysis_wildlandfire_fem_2005.pdf
- Scott, Thompson & Calkin 2013, RMRS-GTR-315 — https://www.fs.usda.gov/rm/pubs/rmrs_gtr315.pdf
- Thompson & Calkin 2011, Uncertainty and risk in wildland fire management — https://research.fs.usda.gov/download/treesearch/38647.pdf
- Calkin, Thompson, Finney & Hyde 2011, WFDSS/FSPro/RAVAR, J. Forestry 109(5) — https://digitalcommons.unl.edu/cgi/viewcontent.cgi?article=1337&context=usdafsfacpub
- Wildfire Risk to Communities v2 methods — https://wildfirerisk.org/wp-content/uploads/2024/05/WildfireRiskToCommunities_V2_Methods_Landscape-wideRisk.pdf
- Technosylva Wildfire Analyst / FireCast — https://technosylva.com/products/wildfire-analyst/firecast/
- CAL FIRE DINS — https://data.ca.gov/dataset/cal-fire-damage-inspection-dins-data
- Oom et al. 2022, Pan-European wildfire risk assessment, JRC130136 — https://publications.jrc.ec.europa.eu/repository/handle/JRC130136
- JRC DRMKC Science for DRM 2020, Portugal 2017 super case study — https://drmkc.jrc.ec.europa.eu/portals/0/Knowledge/ScienceforDRM2020/Files/supercasestudy_04.pdf
- JRC Forest resilience against wildfires — https://publications.jrc.ec.europa.eu/repository/bitstream/JRC145919/JRC145919_01.pdf
- Rockafellar & Uryasev 2000 / 2002, CVaR — https://sites.math.washington.edu/~rtr/papers/rtr179-CVaR1.pdf ; https://sites.math.washington.edu/~rtr/papers/rtr187-CVaR2.pdf
- California DOI wildfire cat-model checklist 2025 — https://www.insurance.ca.gov/01-consumers/180-climate-change/upload/WildfireCatastropheModelChecklist_PRID_20250101.pdf
- NAIC cat-modeling primer 2025 — https://content.naic.org/sites/default/files/committees-pending-action-cat-mod-primer.pdf

Structure loss
- Kramer et al. 2019, IJWF 28:641 — https://doi.org/10.1071/WF18108
- Syphard & Keeley 2019, Fire 2(3):49 — https://doi.org/10.3390/fire2030049
- Knapp et al. 2021, Camp Fire home survival — https://www.fs.usda.gov/psw/publications/knapp/psw_2021_knapp004.pdf
- McAneney et al. 2009, 100 years of Australian bushfire property losses — https://www.naturalhazards.com.au/crc-collection/downloads/mcaneney-et-al-2009.pdf
- AFDRS impact index, IJWF 2025 — https://connectsci.au/wf/article/34/9/WF24148/199836/
- Ribeiro et al. 2020, Pedrógão Grande structures, Fire 3(4):57 — https://www.mdpi.com/2571-6255/3/4/57
- Papakosta, Xanthopoulos & Straub 2017, IJWF 26 — https://www.publish.csiro.au/wf/fulltext/wf15113

Casualties and evacuation
- Haynes et al. 2010 — https://www.sciencedirect.com/science/article/abs/pii/S1462901110000201
- Haynes et al. 2020, Wildfires and WUI fire fatalities — https://research.fs.usda.gov/download/treesearch/60343.pdf
- Blanchi et al. 2014 — https://www.sciencedirect.com/science/article/pii/S1462901113002074
- Molina-Terrén et al. 2019, IJWF 28:85 — https://doi.org/10.1071/WF18004
- Diakakis, Xanthopoulos & Gregos 2016, IJWF 25:797 — https://www.publish.csiro.au/wf/WF15198
- Kalogeropoulos et al. 2025, Mati dire evacuations, Safety Science — https://www.sciencedirect.com/science/article/pii/S0925753524002819
- Cova et al. 2005, trigger points — https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1467-9671.2005.00237.x
- Larsen et al. 2011, WUIVAC — https://content.csbs.utah.edu/~pdennison/pubs/Larsen_AG_WUIVAC.pdf
- Li, Cova & Dennison 2019 — https://content.csbs.utah.edu/~pdennison/reprints/denn/2019_li_etal_ft.pdf
- Egress and wildfire fatalities, PNAS 2026 — https://www.pnas.org/doi/10.1073/pnas.2535081123
- Pedrógão Grande N236 — https://www.iawfonline.org/article/the-open-wounds-of-pedrogao-grande/
- Spain 2025 fires — https://es.wikipedia.org/wiki/Incendios_forestales_de_Espa%C3%B1a_de_2025 ; Torrefeta — https://es.wikipedia.org/wiki/Incendio_de_Torrefeta_de_2025
- Los Gallardos July 2026 — https://www.cnn.com/2026/07/10/europe/wildfires-southern-spain-deaths-intl
- MITECO fatality and loss statistics — https://www.miteco.gob.es/en/biodiversidad/temas/incendios-forestales/seguridad/datos_sobre_seguridad_accidentes.html

Valuation inputs (Spain / Catalonia)
- DGT 2024, Actualización del valor monetario de una vida estadística — https://www.dgt.es/export/sites/web-DGT/.galleries/downloads/conoce_la_dgt/que-hacemos/conocimiento-e-investigacion/Actualizacion-del-valor-monetario-de-una-vida-estadistica-en-Espana.pdf
- DGT 2011 summary — https://seguridadvial2030.dgt.es/practicas-de-interes/respuesta-al-siniestro-efectiva-y-justa/valor-estadistico-de-una-vida-y-una-lesion-no-mortal/index.html
- EUROCONTROL Standard Inputs, VSL — https://ansperformance.eu/economics/cba/standard-inputs/latest/chapters/value_of_a_statistical_life-vsl.html
- EC Better Regulation Toolbox 2023, ch. 8 — https://commission.europa.eu/system/files/2023-09/BRT-2023-Chapter%208-Methodologies%20for%20analysing%20impacts%20in%20IAs%20evaluations%20and%20fitness%20checks_0.pdf
- OECD 2025, Mortality Risk Valuation in Policy Assessment — https://www.oecd.org/content/dam/oecd/en/publications/reports/2025/10/mortality-risk-valuation-in-policy-assessment_733b9d66/76ca89a2-en.pdf
- DGSFP Baremo 2025 — https://dgsfp.mineco.gob.es/es/Regulacion/DocumentosRegulacion/Tablas_indemnizatorias_2025.pdf
- ATC Valors bàsics dels immobles urbans 2025 — https://atc.gencat.cat/web/.content/documents/valoracions/vbasics/2025/valors_basics_urbana_catalunya_2025.pdf
- Catastro INSPIRE BU WFS — https://www.catastro.hacienda.gob.es/webinspire/documentos/inspire-bu-wfs.pdf ; free web services — https://www.catastro.hacienda.gob.es/ws/Webservices_Libres.pdf ; RM methodology — https://www.catastro.hacienda.gob.es/ponencias/Metodologia.pdf
- Equipaments de Catalunya — https://analisi.transparenciacatalunya.cat/Urbanisme-infraestructures/Equipaments-de-Catalunya/8gmd-gz7i
- FEMA Standard Economic Values 2023 — https://www.fema.gov/sites/default/files/documents/fema_standard-economic-values-methodology-report_2023.pdf
- Construction cost indices: Ministerio de Transportes — https://www.transportes.gob.es/informacion-para-el-ciudadano/informacion-estadistica/construccion/indice-de-costes-del-sector-de-la-construccion/indice-de-costes-del-sector-de-la-construccion-cnae-2009-base-2010 ; ACR Dec 2025 — https://acr.es/wp-content/uploads/2026/02/Informe-Costes-Dic-2025.pdf ; ITeC BEDEC 2026 — https://en.itec.cat/infoitec/databases/bedec-construction-2026-now-available/
- RD 300/2004 extraordinary risks — https://www.boe.es/buscar/act.php?id=BOE-A-2004-3373 ; CCS forest fires FAQ — https://www.consorseguros.es/en/preguntas-frecuentes/incendios-forestales ; UNESPA 2025 climate claims — https://www.unespa.es/notasdeprensa/siniestros-climaticos-asegurados-2025/
- JRC 2013 Recording Disaster Losses — https://publications.jrc.ec.europa.eu/repository/handle/JRC83743 ; JRC 2015 guidance — https://publications.jrc.ec.europa.eu/repository/handle/JRC95505
- SDG 11.5.2 / 11.5.3 metadata — https://unstats.un.org/sdgs/metadata/files/Metadata-11-05-02.pdf ; https://unstats.un.org/sdgs/metadata/files/Metadata-11-05-03.pdf
- PGPF Catalunya 2036 memòria — https://agricultura.gencat.cat/web/.content/06-medi-natural/gestio-forestal/enllacos-documents/planificacio/fitxers_estatics/PGPF/Memoria-del-Pla-General-de-Politica-Forestal-de-Catalunya-2036.pdf
- Meier, Elliott & Strobl 2023, JEEM — https://research.birmingham.ac.uk/en/publications/the-regional-economic-impact-of-wildfires-evidence-from-southern-/
- Suppression and restoration cost per ha (press) — https://www.infobae.com/espana/2026/07/25/espana-gasto-hasta-6700-millones-en-apagar-incendios-en-2025-y-en-lo-que-va-de-2026-ya-duplica-las-hectareas-quemadas-en-el-mismo-periodo/

Legal framing
- Ley 17/2015 Sistema Nacional de Protección Civil — https://www.boe.es/buscar/act.php?id=BOE-A-2015-7730
- INFOCAT — https://interior.gencat.cat/web/.content/home/030_arees_dactuacio/proteccio_civil/plans_de_proteccio_civil/plans_de_proteccio_civil_a_catalunya/02-plans-especials/infocat/document_pla_infocat.pdf
- Bombers confine-vs-evacuate doctrine — https://govern.cat/salapremsa/notes-premsa/705782/
