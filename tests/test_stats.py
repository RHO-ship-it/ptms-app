"""
Tests automatisés des calculs statistiques (bibliothèque standard uniquement : python -m unittest).
Lancer depuis la racine du dépôt :  python -m unittest discover -s tests -v
"""
import math
import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import utils as U          # noqa: E402
import scoring as S        # noqa: E402


class HorwitzThompson(unittest.TestCase):
    def test_continuite_aux_bornes(self):
        for c in (1.2e-7, 0.138):
            lo = U.horwitz_thompson_sd(c * (1 - 1e-9), target_value=1.0)
            hi = U.horwitz_thompson_sd(c * (1 + 1e-9), target_value=1.0)
            self.assertAlmostEqual(lo, hi, delta=0.005 * lo)

    def test_coherent_avec_horwitz_classique(self):
        # à C = 1e-6, RSD classique = 2^(1-0,5 log10 C) = 16 %
        self.assertAlmostEqual(U.horwitz_thompson_sd(1e-6, target_value=100.0), 16.0, delta=0.05)

    def test_paliers(self):
        self.assertAlmostEqual(U.horwitz_thompson_sd(1e-8, 100.0), 22.0, places=3)         # 0,22
        self.assertAlmostEqual(U.horwitz_thompson_sd(0.5, 100.0), 0.01 * 0.5 ** -0.5 * 100, places=3)


class StudentEtMandel(unittest.TestCase):
    def test_quantile_student(self):
        self.assertAlmostEqual(U.student_t_two_sided_quantile(0.05, 8), 2.3060, places=3)
        self.assertAlmostEqual(U.student_t_two_sided_quantile(0.05, 1), 12.706, places=2)

    def test_h_critique_table_iso5725(self):
        # ISO 5725-2, tableau des indicateurs h (5 % / 1 %)
        for p, c5, c1 in ((3, 1.15, 1.15), (5, 1.57, 1.72), (10, 1.80, 2.18)):
            self.assertAlmostEqual(U.mandel_h_critical(p, 0.05), c5, delta=0.011)
            self.assertAlmostEqual(U.mandel_h_critical(p, 0.01), c1, delta=0.011)

    def test_grubbs_simple_critique_vs_student(self):
        for n in (7, 20, 50):
            t = U.student_t_two_sided_quantile(0.05 / n, n - 2)          # bilatéral : alpha/(2n) par queue
            ref = (n - 1) / math.sqrt(n) * math.sqrt(t * t / (n - 2 + t * t))
            self.assertAlmostEqual(U.GRUBBS_FULL_TABLE[n][1], ref, delta=0.005)


class Lilliefors(unittest.TestCase):
    def test_valeurs_critiques(self):
        self.assertEqual(U.lilliefors_critical_05(20), 0.190)
        self.assertAlmostEqual(U.lilliefors_critical_05(40), 0.886 / math.sqrt(40), places=6)
        self.assertGreater(U.lilliefors_critical_05(22), U.lilliefors_critical_05(25))

    def test_statistique_D_deux_cotes(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        m, s = sum(x) / 8, U.std_dev(x)
        d = max(max((i + 1) / 8 - U.normal_cdf((v - m) / s), U.normal_cdf((v - m) / s) - i / 8)
                for i, v in enumerate(x))
        self.assertAlmostEqual(U.kolmogorov_smirnov_test(x)["d_max"], round(d, 4), places=4)

    def test_detecte_non_normalite(self):
        self.assertFalse(U.kolmogorov_smirnov_test([1, 1, 1, 1, 1, 1, 1, 1, 1, 50])["is_normal"])


class Kappa(unittest.TestCase):
    def test_accord_parfait(self):
        r = U.compute_fleiss_kappa([["a", "a", "a"], ["b", "b", "b"]])
        self.assertEqual(r["kappa"], 1.0)

    def test_valeur_connue(self):
        # 4 essais, 3 laboratoires : P_i = 1/3, Pe = 0,5 -> kappa = -1/3
        items = [["a", "a", "b"], ["a", "a", "b"], ["b", "b", "a"], ["b", "b", "a"]]
        self.assertAlmostEqual(U.compute_fleiss_kappa(items)["kappa"], -1 / 3, places=3)

    def test_un_seul_essai_non_calculable(self):
        self.assertIsNone(U.compute_fleiss_kappa([["a", "a", "b", "a"]])["kappa"])


class GrubbsDetaille(unittest.TestCase):
    """Jeu de données reproduisant la feuille Excel de référence (p = 7)."""
    DATA = [290, 310, 350, 380, 395, 420, 445]

    def test_iteration_1_comme_excel(self):
        it = U.grubbs_detailed(self.DATA)["iterations"][0]
        self.assertEqual(it["p"], 7)
        self.assertAlmostEqual(it["mean"], 370.0, places=3)
        self.assertAlmostEqual(it["sd"], 56.642, places=3)
        self.assertAlmostEqual(it["G_p"], 1.324, places=3)
        self.assertAlmostEqual(it["G_1"], 1.412, places=3)
        self.assertAlmostEqual(it["crit_1"], 2.139, places=3)
        self.assertAlmostEqual(it["crit_5"], 2.020, places=3)
        d = it["double"]
        self.assertAlmostEqual(d["s0_sq"], 19250.0, places=2)
        self.assertAlmostEqual(d["s_top_sq"], 8000.0, places=2)
        self.assertAlmostEqual(d["s_bot_sq"], 5330.0, places=2)
        self.assertAlmostEqual(d["G_top"], 0.416, places=3)
        self.assertAlmostEqual(d["G_bot"], 0.277, places=3)
        self.assertAlmostEqual(d["crit_1"], 0.031, places=3)
        self.assertAlmostEqual(d["crit_5"], 0.071, places=3)
        self.assertEqual((it["status_top"], it["status_bot"]), ("ok", "ok"))
        self.assertEqual((d["status_top"], d["status_bot"]), ("ok", "ok"))
        self.assertEqual(it["removed"], [])

    def test_iterations_ecartent_les_aberrantes(self):
        data = [250.1, 249.5, 250.7, 249.9, 250.3, 250.0, 249.7, 250.2, 250.4, 249.8, 291.0]
        r = U.grubbs_detailed(data)
        self.assertEqual(r["iterations"][0]["removed"], [291.0])
        self.assertIn(291.0, r["removed"])
        self.assertEqual(r["n_final"], 10)
        self.assertGreaterEqual(len(r["iterations"]), 2)

    def test_coherent_avec_grubbs_test(self):
        data = [250.1, 249.5, 250.7, 249.9, 250.3, 250.0, 249.7, 250.2, 250.4, 249.8, 291.0]
        g = U.grubbs_test(data)
        it = U.grubbs_detailed(data)["iterations"][0]
        self.assertAlmostEqual(max(it["G_p"], it["G_1"]), g["G"], places=3)
        self.assertEqual(it["crit_1"], g["G_critical_1"])


class Scores(unittest.TestCase):
    def test_critere_03_sigma(self):
        z, _ = U.compute_z_score(260, 250, 10, u_xpt=3.0)        # u = 0,3 sigma pile -> z simple
        self.assertAlmostEqual(z, 1.0, places=3)
        zp, _ = U.compute_z_score(260, 250, 10, u_xpt=4.0)       # u > 0,3 sigma -> z'
        self.assertAlmostEqual(zp, 10 / math.sqrt(100 + 16), places=3)

    def test_en(self):
        en, ok = U.compute_en(262, 3.0, 250, 1.5)
        self.assertAlmostEqual(en, 12 / math.sqrt(9 + 2.25), places=3)
        self.assertFalse(ok)

    def test_exclusion_sort_de_l_estimation(self):
        subs = [SimpleNamespace(value=v, excluded_from_stats=(v == 99)) for v in (10, 11, 12, 99)]
        self.assertEqual(S.estimation_values(subs), [10, 11, 12])

    def test_estimateurs_du_rapport(self):
        subs = [SimpleNamespace(value=v, excluded_from_stats=False) for v in (9.8, 10.0, 10.1, 10.2, 9.9, 10.05, 30.0)]
        rows = S.estimator_rows(subs, ["algorithm_a", "median_made", "classical"])
        self.assertEqual(len(rows), 3)
        algo, med, classique = rows
        self.assertLess(abs(algo[1] - 10.0), 0.3)       # robuste : peu influencé par 30,0
        self.assertLess(abs(med[1] - 10.05), 0.1)
        self.assertGreater(classique[1], 12.0)           # moyenne classique tirée par l'aberrante
        self.assertEqual(S.estimator_rows(subs, []), [])


if __name__ == "__main__":
    unittest.main()
