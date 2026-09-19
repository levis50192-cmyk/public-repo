#!/usr/bin/env python3
"""
BINGO BINGO (賓果賓果) 多方法統計分析與回測引擎
==================================================

重要說明（誠實聲明，請勿刪除）：
BINGO BINGO 每期由電腦亂數產生器，從 01~80 隨機不放回抽出 20 個號碼，且每期完全
獨立，不受前一期或任何歷史資料影響。這代表：不存在任何數學方法（機率論、統計學、
精算學、代數、幾何、賽局理論...）能夠讓「下一期」的預測準確率超越均勻亂猜的基準。

這支程式做的事情，是把使用者要求的各種數學角度做成「可比較、可回測」的統計儀表板，
而不是宣稱能預測開獎結果。每個方法算出的「建議號碼」都會拿去跟同一組歷史資料做
回測（walk-forward backtest），並且明確跟「純亂猜基準線」比較命中率。誠實地說，
只要資料夠多，所有方法的回測命中率都應該會收斂到跟基準線幾乎相同——這正是這個
工具要證明給使用者看的事情。

本檔案會被 Claude 排程任務讀取執行，每次有新開獎資料進來時重新計算一次，並把結果
寫回 Artifact 的資料庫（draws collection + stats/latest document）。

用法：
    python3 analysis.py draws.json > stats_output.json

draws.json 格式：
    [{"period": "115052578", "date": "2026-09-17", "time": "07:05",
      "numbers": [1,5,7,...], "superNumber": 45}, ...]
    (numbers 必須是 20 個 1~80 的相異整數，依原始開獎順序或排序皆可)
"""

import sys
import json
import math
import random
from collections import defaultdict, Counter
from itertools import combinations

TOTAL_NUMBERS = 80
DRAWN_PER_ROUND = 20
P_SINGLE = DRAWN_PER_ROUND / TOTAL_NUMBERS  # 0.25，每個號碼單期被開出的機率


# ----------------------------------------------------------------------
# 資料驗證
# ----------------------------------------------------------------------

def validate_draws(draws):
    """結構驗證：擋掉任何格式不對、可能是擷取錯誤的資料列，回傳 (valid, rejected)"""
    valid, rejected = [], []
    seen_periods = set()
    for d in draws:
        try:
            nums = d["numbers"]
            period = d["period"]
            ok = (
                len(nums) == DRAWN_PER_ROUND
                and len(set(nums)) == DRAWN_PER_ROUND
                and all(isinstance(n, int) and 1 <= n <= TOTAL_NUMBERS for n in nums)
                and period not in seen_periods
            )
            sup = d.get("superNumber")
            if sup is not None and sup not in nums:
                ok = False
            if ok:
                seen_periods.add(period)
                valid.append(d)
            else:
                rejected.append(d)
        except (KeyError, TypeError):
            rejected.append(d)
    valid.sort(key=lambda d: d["period"])
    return valid, rejected


# ----------------------------------------------------------------------
# 方法 1：頻率法（統計學 / 古典機率的經驗估計）
# ----------------------------------------------------------------------

def method_frequency(draws, window=None, top_k=10):
    sample = draws[-window:] if window else draws
    counts = Counter()
    for d in sample:
        counts.update(d["numbers"])
    for n in range(1, TOTAL_NUMBERS + 1):
        counts.setdefault(n, 0)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    hot = [n for n, c in ranked[:top_k]]
    cold = [n for n, c in ranked[-top_k:]]
    return {
        "name": "頻率法（熱門號）",
        "field": "統計學",
        "window": len(sample),
        "suggested": hot,
        "cold": cold,
        "counts": dict(counts),
        "note": "取樣本內出現次數最多的號碼。樣本越大，每個號碼的出現次數理論上都會收斂到 window*0.25 附近。",
    }


# ----------------------------------------------------------------------
# 方法 2：遺漏值 / Due 分析（機率論：等待時間為幾何分布）
# ----------------------------------------------------------------------

def method_gap_due(draws, top_k=10):
    last_seen = {}
    for idx, d in enumerate(draws):
        for n in d["numbers"]:
            last_seen[n] = idx
    total = len(draws)
    gaps = {}
    for n in range(1, TOTAL_NUMBERS + 1):
        gaps[n] = (total - 1 - last_seen[n]) if n in last_seen else total
    expected_gap = 1 / P_SINGLE - 1  # 幾何分布期望等待期數 - 1（因為含當期）≈ 3
    ranked = sorted(gaps.items(), key=lambda kv: -kv[1])
    suggested = [n for n, g in ranked[:top_k]]
    return {
        "name": "遺漏值法（該出理論）",
        "field": "機率論（幾何分布）",
        "suggested": suggested,
        "gaps": gaps,
        "expected_gap": round(expected_gap, 2),
        "note": (
            "每個號碼理論期望遺漏值約 %.1f 期。此法選「遺漏最久」的號碼，"
            "但獨立試驗沒有記憶性——遺漏越久不代表下一期更容易開出，"
            "這是統計學上典型的『賭徒謬誤』，放進來是為了讓你回測驗證它不管用。"
        )
        % expected_gap,
    }


# ----------------------------------------------------------------------
# 方法 3：卡方均勻性檢定（統計學 / 精算學：這是「誠實檢查」本身）
# ----------------------------------------------------------------------

def method_chi_square(draws):
    total = len(draws)
    counts = Counter()
    for d in draws:
        counts.update(d["numbers"])
    expected = total * P_SINGLE
    chi2 = sum((counts.get(n, 0) - expected) ** 2 / expected for n in range(1, TOTAL_NUMBERS + 1))
    df = TOTAL_NUMBERS - 1
    p_value = _chi_square_p_value(chi2, df)
    verdict = (
        "沒有證據顯示開獎不均勻（符合官方亂數應有的隨機性）"
        if p_value is None or p_value > 0.05
        else "出現統計上顯著的不均勻——但樣本數過小時很容易出現假陽性，需要更多資料才能下結論"
    )
    return {
        "name": "卡方均勻性檢定",
        "field": "統計學 / 精算學",
        "chi2": round(chi2, 3),
        "df": df,
        "p_value": None if p_value is None else round(p_value, 4),
        "sample_size": total,
        "verdict": verdict,
        "note": "這個檢定不是用來『選號』，而是用來驗證『歷史資料是否符合均勻隨機』。這正是誠實檢查是否有機可乘的核心工具。",
    }


def _chi_square_p_value(chi2, df):
    """用不完全 gamma 函數的近似（Wilson–Hilferty）算 p-value，避免依賴 scipy。"""
    if df <= 0:
        return None
    # Wilson-Hilferty 近似轉成標準常態
    z = (((chi2 / df) ** (1 / 3)) - (1 - 2 / (9 * df))) / math.sqrt(2 / (9 * df))
    # 上尾機率 P(Z > z)
    p = 0.5 * math.erfc(z / math.sqrt(2))
    return max(0.0, min(1.0, p))


# ----------------------------------------------------------------------
# 方法 4：貝氏更新（Beta-Binomial，機率論 / 精算學常用技術）
# ----------------------------------------------------------------------

def method_bayesian(draws, top_k=10):
    total = len(draws)
    counts = Counter()
    for d in draws:
        counts.update(d["numbers"])
    # 先驗：Beta(a0, b0)，用整體理論比例 0.25 當先驗均值，先驗強度設為 20 期等量
    prior_strength = 20
    a0 = prior_strength * P_SINGLE
    b0 = prior_strength * (1 - P_SINGLE)
    posterior = {}
    for n in range(1, TOTAL_NUMBERS + 1):
        k = counts.get(n, 0)
        a = a0 + k
        b = b0 + (total - k)
        posterior[n] = a / (a + b)
    ranked = sorted(posterior.items(), key=lambda kv: -kv[1])
    suggested = [n for n, p in ranked[:top_k]]
    return {
        "name": "貝氏後驗機率法",
        "field": "機率論 / 精算學（Beta-Binomial 模型）",
        "suggested": suggested,
        "posterior": {str(k): round(v, 4) for k, v in posterior.items()},
        "note": "以 Beta(a,b) 先驗結合觀測次數更新每個號碼『長期出現機率』的後驗估計。樣本越多，後驗會越貼近 0.25，跟頻率法結論趨於一致。",
    }


# ----------------------------------------------------------------------
# 方法 5：共現矩陣（代數 / 線性代數：矩陣分析）
# ----------------------------------------------------------------------

def _cooccurrence_matrix(draws):
    co = defaultdict(int)
    for d in draws:
        nums = sorted(d["numbers"])
        for a, b in combinations(nums, 2):
            co[(a, b)] += 1
    return co


def method_cooccurrence(draws, top_k=10, top_pairs=10):
    co = _cooccurrence_matrix(draws)
    total = len(draws)
    expected_pair = total * P_SINGLE * P_SINGLE * (TOTAL_NUMBERS / (TOTAL_NUMBERS - 1))

    # 每個號碼的「共現強度」：跟其他所有號碼的共現次數總和（代數：矩陣列總和）
    strength = Counter()
    for (a, b), c in co.items():
        strength[a] += c
        strength[b] += c
    for n in range(1, TOTAL_NUMBERS + 1):
        strength.setdefault(n, 0)
    ranked_numbers = sorted(strength.items(), key=lambda kv: (-kv[1], kv[0]))
    suggested = [n for n, s in ranked_numbers[:top_k]]

    top_pairs_ranked = sorted(co.items(), key=lambda kv: -kv[1])[:top_pairs]
    pairs = [{"pair": [a, b], "count": c, "expected": round(expected_pair, 2)} for (a, b), c in top_pairs_ranked]

    return {
        "name": "共現矩陣法",
        "field": "代數（矩陣統計）",
        "top_pairs": pairs,
        "suggested": suggested,
        "strength": dict(strength),
        "note": "每個號碼的分數是它跟其他所有號碼的『同期共現次數』總和（矩陣列和）。理論期望單一配對共現次數約 %.2f 次；實際數字若沒有明顯超出，代表號碼之間沒有真實關聯。"
        % expected_pair,
    }


# ----------------------------------------------------------------------
# 方法 8：綜合建議（多方法排名整合 / 這是「共識」不是新的預測力）
# ----------------------------------------------------------------------

def method_consensus(draws, top_k=10):
    """
    把頻率法、遺漏值法、貝氏後驗、共現矩陣 4 種方法對 80 個號碼的排名，
    轉成百分位分數後取平均（Borda-like 排名整合），排出一份「綜合建議」。

    誠實聲明：這不是第 5 種更準的方法，只是把前 4 種意見做平均。如果 4 種方法
    彼此的排名本來就跟真實中獎機率無關（本來就是事實），平均它們也不會產生
    新的預測力——回測會證明這一點。agreement 欄位顯示「這個號碼同時被幾種
    方法列入各自的 Top 10」，純粹描述方法之間的一致程度，不代表命中率更高。
    """
    freq = method_frequency(draws, top_k=TOTAL_NUMBERS)
    gap = method_gap_due(draws, top_k=TOTAL_NUMBERS)
    bayes = method_bayesian(draws, top_k=TOTAL_NUMBERS)
    co = method_cooccurrence(draws, top_k=TOTAL_NUMBERS)

    base_top10 = {
        "frequency": set(freq["suggested"][:10]),
        "gap_due": set(gap["suggested"][:10]),
        "bayesian": set(bayes["suggested"][:10]),
        "cooccurrence": set(co["suggested"][:10]),
    }

    def percentile_scores(ranked_list):
        n = len(ranked_list)
        return {num: (n - idx) / n for idx, num in enumerate(ranked_list)}

    scores = [
        percentile_scores(freq["suggested"]),
        percentile_scores(gap["suggested"]),
        percentile_scores(bayes["suggested"]),
        percentile_scores(co["suggested"]),
    ]

    combined = {}
    for n in range(1, TOTAL_NUMBERS + 1):
        combined[n] = sum(s.get(n, 0) for s in scores) / len(scores)

    ranked = sorted(combined.items(), key=lambda kv: (-kv[1], kv[0]))
    suggested = [n for n, _ in ranked[:top_k]]
    agreement = {
        n: sum(1 for grp in base_top10.values() if n in grp)
        for n in suggested
    }

    return {
        "name": "綜合建議（多方法整合）",
        "field": "跨方法排名整合",
        "suggested": suggested,
        "agreement": agreement,
        "note": (
            "把頻率法、遺漏值法、貝氏後驗、共現矩陣 4 種方法的排名平均起來，"
            "不是第 5 種更準的方法——4 個不準的意見平均起來，理論上還是不準，"
            "下面的回測會顯示這組『綜合建議』命中率一樣貼著亂猜基準線。"
            "agreement 只是說有幾種方法『剛好』也把這個號碼排進自己的前10名，"
            "純粹描述方法間的一致程度，不是命中保證。"
        ),
    }


# ----------------------------------------------------------------------
# 方法 6：蒙地卡羅模擬（電腦模擬統計）
# 誠實更正：這個方法之前被標成「空間幾何統計」其實不準確——它做的事情是電腦隨機抽樣模擬，
# 跟空間幾何沒有關係。現在專案裡已經加了真正的空間幾何方法（方法 8：Moran's I 空間自相關），
# 這裡的標籤改回誠實、準確的「電腦模擬統計」。
# ----------------------------------------------------------------------

def method_monte_carlo(draws, n_sim=2000, seed=42):
    import random

    rng = random.Random(seed)
    total = len(draws)
    if total == 0:
        return {"name": "蒙地卡羅模擬對照", "field": "電腦模擬統計", "note": "資料不足，無法模擬對照。"}

    sim_max_counts = []
    for _ in range(n_sim):
        counts = Counter()
        for _ in range(total):
            counts.update(rng.sample(range(1, TOTAL_NUMBERS + 1), DRAWN_PER_ROUND))
        sim_max_counts.append(max(counts.values()))

    real_counts = Counter()
    for d in draws:
        real_counts.update(d["numbers"])
    real_max = max(real_counts.values()) if real_counts else 0

    sim_max_counts.sort()
    percentile = sum(1 for x in sim_max_counts if x <= real_max) / len(sim_max_counts) * 100

    return {
        "name": "蒙地卡羅模擬對照",
        "field": "電腦模擬統計",
        "n_sim": n_sim,
        "real_max_count": real_max,
        "sim_percentile_of_real_max": round(percentile, 1),
        "sim_median_max_count": sim_max_counts[len(sim_max_counts) // 2],
        "note": (
            "模擬 %d 次『純亂數』開獎，比較真實資料裡『最熱門號碼出現次數』落在模擬分布的第幾百分位。"
            "如果落在中間附近（非極端值），代表真實開獎的『熱門/冷門』現象跟純隨機模擬沒有差異——"
            "也就是說你觀察到的熱門號，本來就是隨機也會自然產生的樣子。"
        )
        % n_sim,
    }


# ----------------------------------------------------------------------
# 方法 7：組合覆蓋（賽局理論 / 組合數學：這是「投注配置」，不是預測）
# ----------------------------------------------------------------------

def method_combinatorial_coverage(pool_size=10):
    # 說明性方法：不依賴歷史資料，純粹是「怎麼分配你的號碼池」的組合數學問題
    from math import comb

    coverage_examples = []
    for k in (1, 2, 3):
        combos_in_pool = comb(pool_size, k)
        combos_total = comb(TOTAL_NUMBERS, k)
        coverage_examples.append(
            {
                "bet_size": k,
                "combos_covered_by_pool": combos_in_pool,
                "combos_total": combos_total,
                "coverage_ratio": round(combos_in_pool / combos_total, 6),
            }
        )
    return {
        "name": "組合覆蓋（投注配置）",
        "field": "賽局理論 / 組合數學",
        "pool_size": pool_size,
        "coverage_examples": coverage_examples,
        "note": (
            "這不是預測方法，而是『如果你要玩，怎麼組合你的號碼池讓相同預算涵蓋更多組合』的組合數學問題。"
            "它不會提高每個號碼被開出的機率（每個號碼永遠是 25%），只影響你賭注的組合覆蓋率跟報酬變異度。"
            "重要提醒：BINGO BINGO 的獎金結構本身是負期望值遊戲（莊家抽成），任何配置方法都無法把負期望值變成正的。"
        ),
    }


# ----------------------------------------------------------------------
# 方法 8：空間自相關（Moran's I）——真正的空間幾何學／空間統計方法
# ----------------------------------------------------------------------
#
# 誠實聲明：BINGO BINGO 開獎本身沒有任何「空間」概念，01~80 只是隨機不放回抽出的
# 標籤數字。這裡把號碼依「賓果賓果選號盤常見的 10 欄 × 8 列排列方式」放到平面座標上，
# 純粹是為了套用真正的空間統計方法（Moran's I 空間自相關指數）而做的畫圖／統計慣例，
# 不代表開獎機制認得這個排列。如果 Moran's I 顯示「沒有空間聚集」，那才是誠實、
# 符合預期的結果——這正是這個方法存在的意義：用真正的空間幾何工具，驗證「盤面排列」
# 跟「開獎結果」沒有關係。

GRID_COLS = 10
GRID_ROWS = 8  # 10 x 8 = 80，賓果賓果選號盤常見排列（僅為統計方法用的座標慣例）


def _grid_pos(n):
    idx = n - 1
    return idx // GRID_COLS, idx % GRID_COLS


def _grid_neighbors(n):
    r, c = _grid_pos(n)
    out = []
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):  # rook 相鄰（上下左右）
        rr, cc = r + dr, c + dc
        if 0 <= rr < GRID_ROWS and 0 <= cc < GRID_COLS:
            out.append(rr * GRID_COLS + cc + 1)
    return out


_GRID_NEIGHBORS = {n: _grid_neighbors(n) for n in range(1, TOTAL_NUMBERS + 1)}
_GRID_EDGES = sorted({
    tuple(sorted((n, nb)))
    for n in range(1, TOTAL_NUMBERS + 1)
    for nb in _GRID_NEIGHBORS[n]
})


def _moran_i(dev):
    """dev：{號碼: 該號碼出現次數 - 平均值}。回傳 Moran's I。"""
    denom = sum(v * v for v in dev.values())
    if denom == 0:
        return 0.0
    num = 0.0
    for a, b in _GRID_EDGES:
        num += 2 * dev[a] * dev[b]
    w_sum = 2 * len(_GRID_EDGES)
    if w_sum == 0:
        return 0.0
    return (TOTAL_NUMBERS / w_sum) * (num / denom)


def method_spatial_autocorrelation(draws, top_k=10, window=None, n_perm=200, seed=99):
    sample = draws[-window:] if window else draws
    if not sample:
        return {
            "name": "空間自相關法（Moran's I）",
            "field": "空間幾何學 / 空間統計學",
            "note": "資料不足，無法計算。",
        }
    counts = Counter()
    for d in sample:
        counts.update(d["numbers"])
    x = {n: counts.get(n, 0) for n in range(1, TOTAL_NUMBERS + 1)}
    xbar = sum(x.values()) / TOTAL_NUMBERS
    dev = {n: x[n] - xbar for n in x}
    observed_i = _moran_i(dev)

    # 排列檢定（permutation test）：把出現次數隨機打散重新分配到盤面座標上 n_perm 次，
    # 算出「完全隨機排列」下 Moran's I 的分布，看真實觀測值落在哪個百分位——
    # 這跟這個工具其他地方已經在用的蒙地卡羅檢定思路一致，只是用在空間統計上。
    rng = random.Random(seed)
    vals = list(x.values())
    keys = list(x.keys())
    more_extreme = 0
    for _ in range(n_perm):
        shuffled = vals[:]
        rng.shuffle(shuffled)
        perm_dev = {k: v - xbar for k, v in zip(keys, shuffled)}
        if abs(_moran_i(perm_dev)) >= abs(observed_i):
            more_extreme += 1
    p_value = (more_extreme + 1) / (n_perm + 1)
    expected_i = -1 / (TOTAL_NUMBERS - 1)  # CSR（完全空間隨機）下 Moran's I 的理論期望值

    # 熱點分數：自己的出現次數跟盤面上相鄰號碼平均出現次數各半，這是 Moran's I
    # 公式本身用到的「空間滯後（spatial lag）」概念，不是另外發明的排名方式。
    lag = {}
    for n in range(1, TOTAL_NUMBERS + 1):
        nbs = _GRID_NEIGHBORS[n]
        lag[n] = (sum(x[m] for m in nbs) / len(nbs)) if nbs else x[n]
    score = {n: 0.5 * x[n] + 0.5 * lag[n] for n in range(1, TOTAL_NUMBERS + 1)}
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    suggested = [n for n, _ in ranked[:top_k]]

    verdict = (
        "沒有證據顯示熱門號在選號盤上有空間聚集（符合完全隨機的預期）"
        if p_value > 0.05
        else "這批樣本的熱門號在選號盤上出現統計上顯著的空間聚集傾向——但這頂多是樣本雜訊，"
             "不代表任何物理機制，資料增加後很可能消失，請用回測結果驗證"
    )

    return {
        "name": "空間自相關法（Moran's I）",
        "field": "空間幾何學 / 空間統計學",
        "suggested": suggested,
        "moran_i": round(observed_i, 4),
        "expected_i_under_randomness": round(expected_i, 4),
        "p_value_permutation": round(p_value, 4),
        "n_permutations": n_perm,
        "verdict": verdict,
        "note": (
            "把 01~80 依賓果賓果選號盤常見的 10欄×8列排列成平面座標，計算 Moran's I 空間自相關指數："
            "檢定『樣本內出現次數高的號碼，在盤面上是不是剛好也跟其他高次數號碼相鄰』。"
            "理論上開獎跟盤面排列（一種人為的視覺慣例）完全無關，Moran's I 應該落在 0 附近，"
            "上面的 p 值是用亂數重新排列 %d 次做排列檢定算出來的。suggested 是『自己出現次數』"
            "跟『盤面相鄰號碼平均出現次數』各半的空間熱點分數——即使 Moran's I 顯示沒有聚集，"
            "這裡還是會照樣選出分數最高的號碼，回測會誠實顯示這樣選跟亂猜沒有差異。"
        ) % n_perm,
    }


# ----------------------------------------------------------------------
# 方法 9：共現矩陣譜分析（特徵值分解）——真正的線性代數方法
# ----------------------------------------------------------------------
#
# 誠實聲明：原本的「共現矩陣法」（方法 5）雖然標榜代數，實際上只用到矩陣的列總和
# （等於矩陣乘以全 1 向量），還稱不上真正的線性代數工具。這裡對同一個共現矩陣做
# 真正的特徵值分解（power iteration + deflation，不依賴 numpy／scipy），找出矩陣
# 最主要的「結構模式」（特徵向量），這才是矩陣代數／譜分析的核心技術。

def _matvec(mat, v, n):
    return [sum(mat[i][j] * v[j] for j in range(n)) for i in range(n)]


def _vnorm(v):
    return math.sqrt(sum(x * x for x in v))


def _power_iteration(mat, n, iters=30, seed=0):
    """對稱矩陣的冪法（power iteration）：回傳（最大特徵值, 對應特徵向量）。"""
    rng = random.Random(seed)
    v = [rng.uniform(-1, 1) for _ in range(n)]
    norm = _vnorm(v) or 1.0
    v = [x / norm for x in v]
    for _ in range(iters):
        v2 = _matvec(mat, v, n)
        norm = _vnorm(v2)
        if norm < 1e-12:
            break
        v = [x / norm for x in v2]
    av = _matvec(mat, v, n)
    eigenvalue = sum(v[i] * av[i] for i in range(n))
    return eigenvalue, v


def method_spectral(draws, top_k=10, window=None, iters=30):
    sample = draws[-window:] if window else draws
    n = TOTAL_NUMBERS
    total = len(sample)
    if total == 0:
        return {
            "name": "共現矩陣譜分析（特徵值分解）",
            "field": "代數學（線性代數・矩陣特徵值分解）",
            "note": "資料不足，無法計算。",
        }

    counts = Counter()
    for d in sample:
        counts.update(d["numbers"])
    freq = [counts.get(i + 1, 0) for i in range(n)]

    co = _cooccurrence_matrix(sample)
    # 建立「殘差矩陣」：實際共現次數 - 假設兩號碼各自獨立時的期望共現次數
    # （期望值用兩者各自出現次數的乘積近似，這跟方法 5 用的期望值同一等級的簡化，
    # 沒有做完整的超幾何分布校正）。對這個殘差矩陣做特徵分解，才是在找「扣掉
    # 各自出現機率之後，還剩下什麼結構」，而不是被『誰出現次數多』這件事主導。
    mat = [[0.0] * n for _ in range(n)]
    for (a, b), c in co.items():
        expected = (freq[a - 1] * freq[b - 1]) / total if total else 0.0
        r = c - expected
        mat[a - 1][b - 1] = r
        mat[b - 1][a - 1] = r

    eig1, v1 = _power_iteration(mat, n, iters=iters, seed=1)
    deflated = [[mat[i][j] - eig1 * v1[i] * v1[j] for j in range(n)] for i in range(n)]
    eig2, v2 = _power_iteration(deflated, n, iters=iters, seed=2)

    frob_sq = sum(mat[i][j] ** 2 for i in range(n) for j in range(n))
    explained = ((eig1 ** 2 + eig2 ** 2) / frob_sq) if frob_sq > 0 else None

    score = {i + 1: math.hypot(v1[i], v2[i]) for i in range(n)}
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    suggested = [num for num, _ in ranked[:top_k]]

    return {
        "name": "共現矩陣譜分析（特徵值分解）",
        "field": "代數學（線性代數・矩陣特徵值分解）",
        "suggested": suggested,
        "eigenvalue_1": round(eig1, 3),
        "eigenvalue_2": round(eig2, 3),
        "explained_variance_ratio_top2": None if explained is None else round(explained, 4),
        "note": (
            "對『共現矩陣 - 各自出現次數推算的期望共現矩陣』做真正的特徵值分解（冪法疊代，"
            "不是矩陣列總和而已），找出殘差矩陣最主要的兩個特徵向量。suggested 是在這兩個"
            "特徵向量上載荷量最大的號碼——如果真實資料是純隨機獨立抽樣，殘差矩陣理論上不該有"
            "穩定的主結構，eigenvalue_1／eigenvalue_2 應該接近雜訊水準、explained_variance_ratio_top2"
            "不該長期偏高。這點一樣要靠下面的回測誠實驗證，不是看公式好聽就代表有效。"
        ),
    }


# ----------------------------------------------------------------------
# 回測框架：walk-forward backtest，誠實比較各方法 vs 純亂猜基準線
# ----------------------------------------------------------------------

def backtest(draws, top_k=10, min_history=20, history_window=200, max_rounds=300):
    """
    對每一期 t（從 min_history 開始），只用「t 之前」的歷史資料算出各方法建議的
    top_k 號碼，然後跟第 t 期實際開出的 20 個號碼比對，算命中幾個。
    最後跟『理論隨機基準』（任選 top_k 個號碼，期望命中數 = top_k * 20/80）比較。

    效能考量：這支程式會被排程每小時重跑一次，資料會持續累積到數千期以上。
    - history_window：每一步計算方法時只看「最近 N 期」歷史（滾動視窗），
      而不是用全部歷史——這樣不管資料累積多久，單步計算量都固定。
    - max_rounds：回測本身最多只跑最近 max_rounds 期（不是全部歷史都重跑一次），
      避免資料量變大後每小時的計算時間跟著無限增加。
    """
    if len(draws) <= min_history:
        return {
            "note": f"目前只有 {len(draws)} 期資料，需要至少 {min_history} 期才能開始回測；資料會隨排程持續累積。",
            "results": {},
            "baseline_expected_hits": round(top_k * P_SINGLE, 3),
        }

    start = max(min_history, len(draws) - max_rounds)
    methods_hits = defaultdict(list)
    for t in range(start, len(draws)):
        history_full = draws[:t]
        history = history_full[-history_window:] if history_window else history_full
        actual = set(draws[t]["numbers"])

        freq = method_frequency(history, top_k=top_k)["suggested"]
        gap = method_gap_due(history, top_k=top_k)["suggested"]
        bayes = method_bayesian(history, top_k=top_k)["suggested"]
        co = method_cooccurrence(history, top_k=top_k)["suggested"]
        consensus = method_consensus(history, top_k=top_k)["suggested"]
        spatial = method_spatial_autocorrelation(history, top_k=top_k)["suggested"]
        spectral = method_spectral(history, top_k=top_k)["suggested"]

        methods_hits["頻率法（熱門號）"].append(len(set(freq) & actual))
        methods_hits["遺漏值法（該出理論）"].append(len(set(gap) & actual))
        methods_hits["貝氏後驗機率法"].append(len(set(bayes) & actual))
        methods_hits["共現矩陣法"].append(len(set(co) & actual))
        methods_hits["綜合建議（多方法整合）"].append(len(set(consensus) & actual))
        methods_hits["空間自相關法（Moran's I）"].append(len(set(spatial) & actual))
        methods_hits["共現矩陣譜分析（特徵值分解）"].append(len(set(spectral) & actual))

    baseline_expected = top_k * P_SINGLE
    results = {}
    for name, hits in methods_hits.items():
        avg_hit = sum(hits) / len(hits)
        results[name] = {
            "rounds_tested": len(hits),
            "avg_hits_per_round": round(avg_hit, 3),
            "baseline_expected_hits": round(baseline_expected, 3),
            "edge_vs_baseline": round(avg_hit - baseline_expected, 3),
        }

    return {
        "rounds_tested": len(draws) - min_history,
        "top_k": top_k,
        "baseline_expected_hits": round(baseline_expected, 3),
        "results": results,
        "note": (
            "edge_vs_baseline 是「該方法平均命中數」減去「純亂猜的理論期望命中數」。"
            "誠實的統計期待：長期下來這個數字應該在 0 附近上下震盪（在統計雜訊範圍內），"
            "如果某個方法長期穩定明顯大於 0，那才是真正值得注意的異常（但目前樣本數還很小，任何差異都可能只是雜訊）。"
        ),
    }


def recent_suggestion_hits(draws, recent_n=100, top_k=10, min_history=20, history_window=200):
    """
    給「開獎歷史紀錄」列表用：針對最近 recent_n 期，每一期都只用『該期之前』
    的歷史資料（跟 backtest() 同樣的滾動視窗 history_window）重算一次
    綜合建議號碼，再跟該期實際開出的 20 個號碼比對，回傳
    {period: [命中的建議號碼...]}（只有命中才會出現在結果裡）。

    這不是新方法，只是把 backtest() 已經在做的「用歷史推薦、跟實際比對」
    這件事，改成保留每一期的命中明細（而不是只算平均），好讓歷史列表可以
    直接標示出「這期開出的號碼裡，有哪些剛好也在當時的建議名單上」。
    """
    n = len(draws)
    if n <= min_history:
        return {}
    start = max(min_history, n - recent_n)
    out = {}
    for t in range(start, n):
        history_full = draws[:t]
        history = history_full[-history_window:] if history_window else history_full
        suggested = set(method_consensus(history, top_k=top_k)["suggested"])
        actual = set(draws[t]["numbers"])
        hits = sorted(suggested & actual)
        if hits:
            out[draws[t]["period"]] = hits
    return out


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------

def run_all(draws_raw, top_k=10):
    valid, rejected = validate_draws(draws_raw)
    latest = valid[-1] if valid else None

    results = {
        "generated_from_rounds": len(valid),
        "rejected_rounds": len(rejected),
        "latest_period": latest["period"] if latest else None,
        "latest_draw": latest,
        "methods": {
            "frequency": method_frequency(valid, top_k=top_k),
            "gap_due": method_gap_due(valid, top_k=top_k),
            "chi_square": method_chi_square(valid),
            "bayesian": method_bayesian(valid, top_k=top_k),
            "cooccurrence": method_cooccurrence(valid, top_k=top_k),
            "consensus": method_consensus(valid, top_k=top_k),
            "spatial": method_spatial_autocorrelation(valid, top_k=top_k),
            "spectral": method_spectral(valid, top_k=top_k),
            "monte_carlo": method_monte_carlo(valid),
            "combinatorial_coverage": method_combinatorial_coverage(),
        },
        "backtest": backtest(valid, top_k=top_k),
    }

    # 上一期「建議號碼」對上「這一期實際開獎」的命中結果，給前端做顏色標示
    if len(valid) >= 2:
        prev_history = valid[:-1]
        actual = set(valid[-1]["numbers"])
        hit_check = {}
        for key, fn in (
            ("frequency", lambda h: method_frequency(h, top_k=top_k)["suggested"]),
            ("gap_due", lambda h: method_gap_due(h, top_k=top_k)["suggested"]),
            ("bayesian", lambda h: method_bayesian(h, top_k=top_k)["suggested"]),
            ("cooccurrence", lambda h: method_cooccurrence(h, top_k=top_k)["suggested"]),
            ("consensus", lambda h: method_consensus(h, top_k=top_k)["suggested"]),
            ("spatial", lambda h: method_spatial_autocorrelation(h, top_k=top_k)["suggested"]),
            ("spectral", lambda h: method_spectral(h, top_k=top_k)["suggested"]),
        ):
            suggested_prev = fn(prev_history)
            hit_check[key] = {
                "suggested": suggested_prev,
                "hits": sorted(set(suggested_prev) & actual),
                "hit_count": len(set(suggested_prev) & actual),
            }
        results["last_round_hit_check"] = hit_check

    return results


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 analysis.py draws.json", file=sys.stderr)
        sys.exit(1)
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        raw = json.load(f)
    out = run_all(raw)
    print(json.dumps(out, ensure_ascii=False, indent=2))
