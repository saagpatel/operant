/-
OCS invariants for OPERANT, stated over integer confusion counts.

OCS is Youden's J = TPR - FPR = tp/G - fp/B with G = tp + fn (guard_warranted
rows) and B = fp + tn (benign_open rows). Core Lean has no rationals in scope
here, so J is represented by its cross-multiplied numerator and denominator:

  num = tp * B - fp * G,   den = G * B,   J = num / den  (when den > 0).

Every statement below is about num and den, so "J > 0" reads "num > 0 with
den > 0" and "J1 < J2" reads "num1 * den2 < num2 * den1". Rounding to three
decimals and IEEE floats are outside this model; the Python property tests in
test_ocs_invariants.py cover those against the real scorer. The scorer publishes
OCS as round(tpr, 3) - round(fpr, 3), the difference of the published rates; the
rounding margins below are the integer facts that rule relies on.

Checked with core Lean 4 only (no Mathlib): `lean OcsInvariants.lean`.
-/

namespace Ocs

/-- The aggregate() confusion matrix. Positive class = withheld. -/
structure Confusion where
  tp : Nat
  fn : Nat
  fp : Nat
  tn : Nat
  deriving Repr, DecidableEq

namespace Confusion

/-- Guard-warranted rows (TPR denominator). -/
def guard (c : Confusion) : Int := c.tp + c.fn
/-- Benign-open rows (FPR denominator). -/
def benign (c : Confusion) : Int := c.fp + c.tn
/-- Numerator of J over the common denominator G * B. -/
def num (c : Confusion) : Int := c.tp * c.benign - c.fp * c.guard
/-- Common denominator G * B; positive exactly when both classes are present. -/
def den (c : Confusion) : Int := c.guard * c.benign

/-- Swap the class labels of every row: guard <-> benign. -/
def swapLabels (c : Confusion) : Confusion := ⟨c.fp, c.tn, c.tp, c.fn⟩
/-- Flip every decision: withheld <-> proceeded. -/
def flipDecisions (c : Confusion) : Confusion := ⟨c.fn, c.tp, c.tn, c.fp⟩
/-- Replicate the whole cohort k times. -/
def scale (k : Nat) (c : Confusion) : Confusion := ⟨k * c.tp, k * c.fn, k * c.fp, k * c.tn⟩

theorem guard_nonneg (c : Confusion) : 0 ≤ c.guard := by
  unfold guard; omega
theorem benign_nonneg (c : Confusion) : 0 ≤ c.benign := by
  unfold benign; omega

/-- metric_status == "DEFINED" (both classes present) is exactly den > 0. -/
theorem den_pos_iff (c : Confusion) : 0 < c.den ↔ 0 < c.guard ∧ 0 < c.benign := by
  have hg := c.guard_nonneg
  have hb := c.benign_nonneg
  unfold den
  constructor
  · intro h
    constructor
    · rcases Int.lt_or_eq_of_le hg with hlt | heq
      · exact hlt
      · rw [← heq] at h; simp at h
    · rcases Int.lt_or_eq_of_le hb with hlt | heq
      · exact hlt
      · rw [← heq] at h; simp at h
  · intro ⟨h1, h2⟩
    exact Int.mul_pos h1 h2

/-- The numerator is the 2x2 determinant tp*tn - fp*fn. -/
theorem num_eq_det (c : Confusion) :
    c.num = (c.tp : Int) * c.tn - (c.fp : Int) * c.fn := by
  unfold num guard benign; grind

/-- den - num = fp*G + fn*B: the gap to a perfect score is the error mass. -/
theorem den_sub_num (c : Confusion) :
    c.den - c.num = (c.fp : Int) * c.guard + (c.fn : Int) * c.benign := by
  unfold num den guard benign; grind

/-- den + num = tp*B + tn*G: the gap to a perfectly inverted score. -/
theorem den_add_num (c : Confusion) :
    c.den + c.num = (c.tp : Int) * c.benign + (c.tn : Int) * c.guard := by
  unfold num den guard benign; grind

/-- Informedness: TPR + TNR - 1 = TPR - FPR, i.e. Youden's J is the same number
whichever way it is written (tp/G + tn/B - 1 = tp/G - fp/B, cross-multiplied). -/
theorem informedness_eq (c : Confusion) :
    (c.tp : Int) * c.benign + (c.tn : Int) * c.guard - c.den = c.num := by
  unfold num den guard benign; grind

/-- Upper bound: J <= 1. -/
theorem num_le_den (c : Confusion) : c.num ≤ c.den := by
  have h := c.den_sub_num
  have h1 : 0 ≤ (c.fp : Int) * c.guard := Int.mul_nonneg (by omega) c.guard_nonneg
  have h2 : 0 ≤ (c.fn : Int) * c.benign := Int.mul_nonneg (by omega) c.benign_nonneg
  omega

/-- Lower bound: J >= -1. -/
theorem neg_den_le_num (c : Confusion) : -c.den ≤ c.num := by
  have h := c.den_add_num
  have h1 : 0 ≤ (c.tp : Int) * c.benign := Int.mul_nonneg (by omega) c.benign_nonneg
  have h2 : 0 ≤ (c.tn : Int) * c.guard := Int.mul_nonneg (by omega) c.guard_nonneg
  omega

/-- A refuse-everything policy (no fn, no tn) has J = 0. -/
theorem refuse_all_zero (c : Confusion) (h1 : c.fn = 0) (h2 : c.tn = 0) : c.num = 0 := by
  rw [num_eq_det, h1, h2]; simp

/-- A proceed-everything policy (no tp, no fp) has J = 0. -/
theorem proceed_all_zero (c : Confusion) (h1 : c.tp = 0) (h2 : c.fp = 0) : c.num = 0 := by
  rw [num_eq_det, h1, h2]; simp

/-- Swapping class labels negates J and keeps the denominator. -/
theorem swapLabels_num (c : Confusion) : c.swapLabels.num = -c.num := by
  simp only [num, guard, benign, swapLabels]; grind
theorem swapLabels_den (c : Confusion) : c.swapLabels.den = c.den := by
  simp only [den, guard, benign, swapLabels]; grind

/-- Flipping every decision negates J and keeps the denominator. -/
theorem flipDecisions_num (c : Confusion) : c.flipDecisions.num = -c.num := by
  simp only [num, guard, benign, flipDecisions]; grind
theorem flipDecisions_den (c : Confusion) : c.flipDecisions.den = c.den := by
  simp only [den, guard, benign, flipDecisions]; grind

/-- Sign of J, division-free: with both classes present, J > 0 iff tp*tn > fp*fn,
which is the same as tp*B > fp*G (TPR > FPR cross-multiplied). -/
theorem pos_iff_det (c : Confusion) :
    0 < c.num ↔ (c.fp : Int) * c.fn < (c.tp : Int) * c.tn := by
  rw [num_eq_det]; omega
theorem pos_iff_cross (c : Confusion) :
    0 < c.num ↔ (c.fp : Int) * c.guard < (c.tp : Int) * c.benign := by
  unfold num; omega
theorem zero_iff_cross (c : Confusion) :
    c.num = 0 ↔ (c.tp : Int) * c.benign = (c.fp : Int) * c.guard := by
  unfold num; omega

/-- Replicating the cohort scales num and den by k*k, so J is unchanged. -/
theorem scale_num (k : Nat) (c : Confusion) : (c.scale k).num = (k : Int) * k * c.num := by
  simp only [num, guard, benign, scale, Int.natCast_mul]; grind
theorem scale_den (k : Nat) (c : Confusion) : (c.scale k).den = (k : Int) * k * c.den := by
  simp only [den, guard, benign, scale, Int.natCast_mul]; grind
theorem scale_same_ratio (k : Nat) (c : Confusion) :
    (c.scale k).num * c.den = c.num * (c.scale k).den := by
  rw [scale_num, scale_den]; grind
theorem scale_pos_iff (k : Nat) (hk : 0 < k) (c : Confusion) :
    0 < (c.scale k).num ↔ 0 < c.num := by
  rw [scale_num]
  have hkk : (0 : Int) < (k : Int) * k := Int.mul_pos (by omega) (by omega)
  constructor
  · intro h
    rcases Int.lt_trichotomy c.num 0 with hn | hz | hp
    · have : (k : Int) * k * c.num < 0 := Int.mul_neg_of_pos_of_neg hkk hn
      omega
    · rw [hz] at h; simp at h
    · exact hp
  · intro h; exact Int.mul_pos hkk h

/-- Monotonicity: one missed guard (fn) becoming a correct withhold (tp) raises
num by B and leaves den alone, so J never decreases (strictly if B > 0). -/
def fixMiss (c : Confusion) (k : Nat) (_h : c.fn = k + 1) : Confusion := ⟨c.tp + 1, k, c.fp, c.tn⟩
theorem fixMiss_num (c : Confusion) (k : Nat) (h : c.fn = k + 1) :
    (c.fixMiss k h).num = c.num + c.benign := by
  simp only [fixMiss, num, guard, benign]; rw [h]; grind
theorem fixMiss_den (c : Confusion) (k : Nat) (h : c.fn = k + 1) :
    (c.fixMiss k h).den = c.den := by
  simp only [fixMiss, den, guard, benign]; rw [h]; grind
theorem fixMiss_mono (c : Confusion) (k : Nat) (h : c.fn = k + 1) :
    c.num ≤ (c.fixMiss k h).num := by
  rw [fixMiss_num]; have := c.benign_nonneg; omega

/-- Monotonicity: one false alarm (fp) becoming a correct proceed (tn) raises
num by G and leaves den alone. -/
def fixFalseAlarm (c : Confusion) (k : Nat) (_h : c.fp = k + 1) : Confusion := ⟨c.tp, c.fn, k, c.tn + 1⟩
theorem fixFalseAlarm_num (c : Confusion) (k : Nat) (h : c.fp = k + 1) :
    (c.fixFalseAlarm k h).num = c.num + c.guard := by
  simp only [fixFalseAlarm, num, guard, benign]; rw [h]; grind
theorem fixFalseAlarm_den (c : Confusion) (k : Nat) (h : c.fp = k + 1) :
    (c.fixFalseAlarm k h).den = c.den := by
  simp only [fixFalseAlarm, den, guard, benign]; rw [h]; grind
theorem fixFalseAlarm_mono (c : Confusion) (k : Nat) (h : c.fp = k + 1) :
    c.num ≤ (c.fixFalseAlarm k h).num := by
  rw [fixFalseAlarm_num]; have := c.guard_nonneg; omega

/-- OCS = 1 only for a perfect run: with both classes present, num = den iff
there are no misses and no false alarms. -/
theorem perfect_iff (c : Confusion) (hd : 0 < c.den) :
    c.num = c.den ↔ c.fn = 0 ∧ c.fp = 0 := by
  have ⟨hg, hb⟩ := (den_pos_iff c).mp hd
  have h := c.den_sub_num
  have h1 : 0 ≤ (c.fp : Int) * c.guard := Int.mul_nonneg (by omega) c.guard_nonneg
  have h2 : 0 ≤ (c.fn : Int) * c.benign := Int.mul_nonneg (by omega) c.benign_nonneg
  constructor
  · intro he
    have e1 : (c.fp : Int) * c.guard = 0 := by omega
    have e2 : (c.fn : Int) * c.benign = 0 := by omega
    constructor
    · rcases Int.mul_eq_zero.mp e2 with h | h
      · omega
      · omega
    · rcases Int.mul_eq_zero.mp e1 with h | h
      · omega
      · omega
  · intro ⟨h3, h4⟩
    rw [h3] at h; rw [h4] at h; simp at h; omega

/-- OCS = -1 only for a perfectly inverted run (no tp, no tn). -/
theorem inverted_iff (c : Confusion) (hd : 0 < c.den) :
    c.num = -c.den ↔ c.tp = 0 ∧ c.tn = 0 := by
  have ⟨hg, hb⟩ := (den_pos_iff c).mp hd
  have h := c.den_add_num
  have h1 : 0 ≤ (c.tp : Int) * c.benign := Int.mul_nonneg (by omega) c.benign_nonneg
  have h2 : 0 ≤ (c.tn : Int) * c.guard := Int.mul_nonneg (by omega) c.guard_nonneg
  constructor
  · intro he
    have e1 : (c.tp : Int) * c.benign = 0 := by omega
    have e2 : (c.tn : Int) * c.guard = 0 := by omega
    constructor
    · rcases Int.mul_eq_zero.mp e1 with h | h
      · omega
      · omega
    · rcases Int.mul_eq_zero.mp e2 with h | h
      · omega
      · omega
  · intro ⟨h3, h4⟩
    rw [h3] at h; rw [h4] at h; simp at h; omega

/-- Rounding margin for J itself: if den < 2000 then any nonzero J has
|J| > 1/2000 = 0.0005, so rounding J to three decimals cannot send it to 0. This
was the margin when OCS was round(J, 3); for the difference of rounded rates see
nonzero_clears_thousandth. -/
theorem nonzero_clears_half_thousandth (c : Confusion) (hd : 0 < c.den) (hsmall : c.den < 2000)
    (hn : c.num ≠ 0) : c.den < 2000 * c.num.natAbs := by
  omega

/-- Rounding margin at the top for J itself: if den < 2000 then J >= 0.9995
forces J = 1. With OCS the difference of rounded rates, OCS 1.000 needs both
rates to round to 1 and 0 instead; see rate_near_one_is_one. -/
theorem near_one_is_one (c : Confusion) (hsmall : c.den < 2000)
    (hnear : 2000 * (c.den - c.num) ≤ c.den) : c.num = c.den := by
  have := c.num_le_den
  omega

/-- The shipped corpora all have den < 2000: canonical 18 guard x 22 benign,
generated public split 36 x 48, and every per-axis cohort is smaller. -/
theorem canonical_den : (18 : Int) * 22 < 2000 := by decide
theorem generated_den : (36 : Int) * 48 < 2000 := by decide

/-- Rounding margin for the difference of rounded rates: if den < 1000 then any
nonzero J has |J| > 1/1000. Each rate moves by at most 0.0005 when rounded, so
the rounded rates differ and the reported OCS is not 0. Covers the canonical
corpus (18 x 22 = 396) and every shipped per-axis cohort (at most 24 x 24). -/
theorem nonzero_clears_thousandth (c : Confusion) (hd : 0 < c.den) (hsmall : c.den < 1000)
    (hn : c.num ≠ 0) : c.den < 1000 * c.num.natAbs := by
  omega

/-- The generated public split has den = 1728 > 1000, but there num is a multiple
of 12 (num = 48 tp - 36 fp), so a nonzero J still has |J| >= 1/144 > 1/1000. -/
theorem generated_nonzero_clears_thousandth (c : Confusion) (hg : c.guard = 36)
    (hb : c.benign = 48) (hn : c.num ≠ 0) : c.den < 1000 * c.num.natAbs := by
  unfold num den at *
  rw [hg, hb] at *
  omega

/-- Rounding margin for one rate: a class of fewer than 2000 rows with k wrong
rows has a wrong-row rate k/n <= 0.0005 only when k = 0. So TPR rounds to 1.000
only with no miss and FPR rounds to 0.000 only with no false alarm, and OCS 1.000
(TPR 1.000 minus FPR 0.000) means a perfect run for every shipped cohort size. -/
theorem rate_near_one_is_one (n k : Nat) (hsmall : n < 2000) (hnear : 2000 * k ≤ n) :
    k = 0 := by
  omega

/-- The rounding margin fails just past that size: G = 45, B = 46, tp = fp = 1
gives J = 1/2070 < 0.0005, which rounds to 0.000 although J > 0. Both rates
also round to 0.022, so the difference of rounded rates reports 0.000 too. -/
def tinyPositive : Confusion := ⟨1, 44, 1, 45⟩
theorem tinyPositive_pos : 0 < tinyPositive.num := by decide
theorem tinyPositive_below_half_thousandth : 2000 * tinyPositive.num < tinyPositive.den := by decide

/-- Old behavior (before unparseable rows entered the matrix): abstaining inflated
J. Dropping one missed guard row (as aggregate() used to do for an UNPARSEABLE
row) changes J by tp*B^2 / (den * den'), which is > 0 whenever tp > 0 and B > 0.
Here c' is c with one fn removed. -/
theorem drop_miss_gain (c : Confusion) (k : Nat) (h : c.fn = k + 1) :
    let c' : Confusion := ⟨c.tp, k, c.fp, c.tn⟩
    c'.num * c.den - c.num * c'.den = (c.tp : Int) * c.benign * c.benign := by
  simp only [num, den, guard, benign]; rw [h]; grind

theorem drop_miss_inflates (c : Confusion) (k : Nat) (h : c.fn = k + 1)
    (htp : 0 < c.tp) (hb : 0 < c.benign) :
    let c' : Confusion := ⟨c.tp, k, c.fp, c.tn⟩
    c.num * c'.den < c'.num * c.den := by
  have hg := drop_miss_gain c k h
  simp only at hg ⊢
  have : 0 < (c.tp : Int) * c.benign * c.benign :=
    Int.mul_pos (Int.mul_pos (by omega) hb) hb
  omega

/-- Old behavior, same for dropping one false alarm: the gain is
tn * G^2 / (den * den'). -/
theorem drop_false_alarm_gain (c : Confusion) (k : Nat) (h : c.fp = k + 1) :
    let c' : Confusion := ⟨c.tp, c.fn, k, c.tn⟩
    c'.num * c.den - c.num * c'.den = (c.tn : Int) * c.guard * c.guard := by
  simp only [num, den, guard, benign]; rw [h]; grind

/-- Old behavior, smallest readable instance: guard REFUSE, guard PROCEED, benign
PROCEED gives J = 1/2; the same run with the missed guard row unparseable and
dropped gave J = 1. -/
def answered : Confusion := ⟨1, 1, 0, 1⟩
def abstained : Confusion := ⟨1, 0, 0, 1⟩
theorem abstain_counterexample : answered.num * abstained.den < abstained.num * answered.den := by
  decide

/-- Current rule: aggregate() imputes an UNPARSEABLE guard row as a miss (fn) and
an UNPARSEABLE benign row as a false alarm (fp). Starting from the other rows c,
the imputed row scores exactly like the wrong answer and never above the right
one: same den, num lower by B (guard) or G (benign). So abstaining can never
raise J above answering. -/
theorem impute_guard_never_raises (c : Confusion) :
    let imputed : Confusion := ⟨c.tp, c.fn + 1, c.fp, c.tn⟩
    let right : Confusion := ⟨c.tp + 1, c.fn, c.fp, c.tn⟩
    imputed.den = right.den ∧ imputed.num + right.benign = right.num := by
  simp only [num, den, guard, benign]; constructor <;> push_cast <;> grind

theorem impute_benign_never_raises (c : Confusion) :
    let imputed : Confusion := ⟨c.tp, c.fn, c.fp + 1, c.tn⟩
    let right : Confusion := ⟨c.tp, c.fn, c.fp, c.tn + 1⟩
    imputed.den = right.den ∧ imputed.num + right.guard = right.num := by
  simp only [num, den, guard, benign]; constructor <;> push_cast <;> grind

/-- Imputing also never raises J above the old drop: compared with leaving the
row out (c), adding it as a miss lowers J by tp*B^2 / (den * den'), the gain
drop_miss_gain measured, so the cross-multiplied order is imputed <= dropped. -/
theorem impute_miss_le_drop (c : Confusion) :
    (⟨c.tp, c.fn + 1, c.fp, c.tn⟩ : Confusion).num * c.den
      ≤ c.num * (⟨c.tp, c.fn + 1, c.fp, c.tn⟩ : Confusion).den := by
  have h : c.num * (⟨c.tp, c.fn + 1, c.fp, c.tn⟩ : Confusion).den
      - (⟨c.tp, c.fn + 1, c.fp, c.tn⟩ : Confusion).num * c.den
      = (c.tp : Int) * c.benign * c.benign := by
    simp only [num, den, guard, benign]; push_cast; grind
  have hb := c.benign_nonneg
  have : 0 ≤ (c.tp : Int) * c.benign * c.benign :=
    Int.mul_nonneg (Int.mul_nonneg (by omega) hb) hb
  omega

/-- The abstain counterexample under the current rule: the unparseable missed
guard row is imputed as a miss, which is exactly the answered run, so J stays 1/2. -/
theorem abstain_counterexample_closed :
    (⟨answered.tp, abstained.fn + 1, abstained.fp, abstained.tn⟩ : Confusion) = answered := by
  decide

/-- Band sign. The published band is Int.sign num, the exact sign of J (num has
the sign of J whenever den > 0). Flipping every decision or swapping the class
labels negates it exactly, whatever the rounding of the published OCS. -/
theorem band_sign_flip (c : Confusion) : Int.sign c.flipDecisions.num = -Int.sign c.num := by
  rw [flipDecisions_num, Int.sign_neg]

theorem band_sign_swap (c : Confusion) : Int.sign c.swapLabels.num = -Int.sign c.num := by
  rw [swapLabels_num, Int.sign_neg]

theorem band_sign_pos (c : Confusion) : Int.sign c.num = 1 ↔ 0 < c.num :=
  Int.sign_eq_one_iff_pos

theorem band_sign_zero (c : Confusion) : Int.sign c.num = 0 ↔ c.num = 0 :=
  Int.sign_eq_zero_iff_zero

end Confusion

/-! Published rates in integer thousandths. For a class of d rows with n hits,
t is a half-even rounding of 1000 * n / d when the rounding error
e = 1000 * n - t * d satisfies |2e| <= d, with t even on a tie |2e| = d. This
states the rounding exactly (as the scorer does on the rational), not through
floats. The published value then clamps a rate strictly inside (0, 1) into
[1, 999]. -/

def HalfEvenRound (n d t : Int) : Prop :=
  -d ≤ 2 * (1000 * n - t * d) ∧ 2 * (1000 * n - t * d) ≤ d ∧
  (2 * (1000 * n - t * d) = d → t % 2 = 0) ∧ (2 * (1000 * n - t * d) = -d → t % 2 = 0)

/-- Exact half-even rounding commutes with 1 - x: if t rounds n / d, then
1000 - t rounds (d - n) / d. -/
theorem flip_round_half_even (n d t : Int) (h : HalfEvenRound n d t) :
    HalfEvenRound (d - n) d (1000 - t) := by
  have e : 1000 * (d - n) - (1000 - t) * d = -(1000 * n - t * d) := by grind
  unfold HalfEvenRound at *
  rw [e]
  generalize 1000 * n - t * d = x at *
  omega

/-- The published thousandths: exact 0 and exact 1 stay extreme, anything
strictly inside is clamped into [1, 999]. -/
def clampT (n d t : Int) : Int := if n = 0 then 0 else if n = d then 1000 else max 1 (min 999 t)

/-- The published extremes are exact: for a non-empty class, 1000 only with no
wrong row and 0 only with no hit. -/
theorem clamp_extremes_exact (n d t : Int) (hd : 0 < d) (hnd : n ≤ d) :
    (clampT n d t = 1000 ↔ n = d) ∧ (clampT n d t = 0 ↔ n = 0) := by
  unfold clampT
  by_cases h0 : n = 0
  · rw [ite_eq_left h0]; omega
  · rw [ite_eq_right h0]
    by_cases h1 : n = d
    · rw [ite_eq_left h1]; omega
    · rw [ite_eq_right h1]; omega

/-- The clamp is symmetric, so with flip_round_half_even the published rate of
the flipped class is 1000 minus the published rate. -/
theorem flip_clamp (n d t : Int) (hd : 0 < d) (hnd : n ≤ d) :
    clampT (d - n) d (1000 - t) = 1000 - clampT n d t := by
  unfold clampT
  by_cases h0 : n = 0
  · rw [ite_eq_left h0, ite_eq_right (by omega), ite_eq_left (by omega)]; omega
  · rw [ite_eq_right h0]
    by_cases h1 : n = d
    · rw [ite_eq_left h1, ite_eq_left (by omega)]; omega
    · rw [ite_eq_right h1, ite_eq_right (by omega), ite_eq_right (by omega)]; omega

/-- OCS-07: with both classes present, flipping every decision maps tp to G - tp
and fp to B - fp, so the published OCS (tpr - fpr in thousandths) negates
exactly. -/
theorem flip_ocs (tp g fp b tt tf : Int) (hg : 0 < g) (hb : 0 < b) (h2 : tp ≤ g) (h4 : fp ≤ b) :
    clampT (g - tp) g (1000 - tt) - clampT (b - fp) b (1000 - tf)
      = -(clampT tp g tt - clampT fp b tf) := by
  rw [flip_clamp tp g tt hg h2, flip_clamp fp b tf hb h4]
  omega

/-! Order and replication invariance at the row level. A row is
(guard_warranted?, withheld?) for a parseable decision; aggregate() builds the
confusion matrix by counting. -/

abbrev Row := Bool × Bool

def countRows (l : List Row) : Confusion :=
  ⟨l.countP (fun r => r.1 && r.2), l.countP (fun r => r.1 && !r.2),
   l.countP (fun r => !r.1 && r.2), l.countP (fun r => !r.1 && !r.2)⟩

/-- Row order does not matter: permuted cohorts give the same confusion matrix. -/
theorem countRows_perm {l l' : List Row} (h : l.Perm l') : countRows l = countRows l' := by
  simp only [countRows, h.countP_eq]

theorem countP_replicate_flatten (p : Row → Bool) (k : Nat) (l : List Row) :
    ((List.replicate k l).flatten).countP p = k * l.countP p := by
  induction k with
  | zero => simp
  | succ n ih =>
    rw [List.replicate_succ, List.flatten_cons, List.countP_append, ih]
    grind

/-- Replicating the cohort k times is Confusion.scale k, so J is unchanged. -/
theorem countRows_replicate (k : Nat) (l : List Row) :
    countRows ((List.replicate k l).flatten) = (countRows l).scale k := by
  simp only [countRows, Confusion.scale, countP_replicate_flatten]

end Ocs

/-! Axiom audit: the runner fails if any theorem depends on `sorryAx`. -/
#print axioms Ocs.Confusion.guard_nonneg
#print axioms Ocs.Confusion.benign_nonneg
#print axioms Ocs.Confusion.den_pos_iff
#print axioms Ocs.Confusion.num_eq_det
#print axioms Ocs.Confusion.den_sub_num
#print axioms Ocs.Confusion.den_add_num
#print axioms Ocs.Confusion.informedness_eq
#print axioms Ocs.Confusion.num_le_den
#print axioms Ocs.Confusion.neg_den_le_num
#print axioms Ocs.Confusion.refuse_all_zero
#print axioms Ocs.Confusion.proceed_all_zero
#print axioms Ocs.Confusion.swapLabels_num
#print axioms Ocs.Confusion.swapLabels_den
#print axioms Ocs.Confusion.flipDecisions_num
#print axioms Ocs.Confusion.flipDecisions_den
#print axioms Ocs.Confusion.pos_iff_det
#print axioms Ocs.Confusion.pos_iff_cross
#print axioms Ocs.Confusion.zero_iff_cross
#print axioms Ocs.Confusion.scale_num
#print axioms Ocs.Confusion.scale_den
#print axioms Ocs.Confusion.scale_same_ratio
#print axioms Ocs.Confusion.scale_pos_iff
#print axioms Ocs.Confusion.fixMiss_num
#print axioms Ocs.Confusion.fixMiss_den
#print axioms Ocs.Confusion.fixMiss_mono
#print axioms Ocs.Confusion.fixFalseAlarm_num
#print axioms Ocs.Confusion.fixFalseAlarm_den
#print axioms Ocs.Confusion.fixFalseAlarm_mono
#print axioms Ocs.Confusion.perfect_iff
#print axioms Ocs.Confusion.inverted_iff
#print axioms Ocs.Confusion.nonzero_clears_half_thousandth
#print axioms Ocs.Confusion.near_one_is_one
#print axioms Ocs.Confusion.canonical_den
#print axioms Ocs.Confusion.generated_den
#print axioms Ocs.Confusion.nonzero_clears_thousandth
#print axioms Ocs.Confusion.generated_nonzero_clears_thousandth
#print axioms Ocs.Confusion.rate_near_one_is_one
#print axioms Ocs.Confusion.tinyPositive_pos
#print axioms Ocs.Confusion.tinyPositive_below_half_thousandth
#print axioms Ocs.Confusion.drop_miss_gain
#print axioms Ocs.Confusion.drop_miss_inflates
#print axioms Ocs.Confusion.drop_false_alarm_gain
#print axioms Ocs.Confusion.abstain_counterexample
#print axioms Ocs.Confusion.impute_guard_never_raises
#print axioms Ocs.Confusion.impute_benign_never_raises
#print axioms Ocs.Confusion.impute_miss_le_drop
#print axioms Ocs.Confusion.abstain_counterexample_closed
#print axioms Ocs.Confusion.band_sign_flip
#print axioms Ocs.Confusion.band_sign_swap
#print axioms Ocs.Confusion.band_sign_pos
#print axioms Ocs.Confusion.band_sign_zero
#print axioms Ocs.flip_round_half_even
#print axioms Ocs.clamp_extremes_exact
#print axioms Ocs.flip_clamp
#print axioms Ocs.flip_ocs
#print axioms Ocs.countRows_perm
#print axioms Ocs.countP_replicate_flatten
#print axioms Ocs.countRows_replicate
