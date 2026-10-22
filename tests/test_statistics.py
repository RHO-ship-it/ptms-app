import unittest
import math
from utils import (
    robust_stats,
    grubbs_test,
    kolmogorov_smirnov_test,
    mandel_h_statistic,
    compute_cohen_kappa,
    horwitz_rsd,
    compute_z_score,
    compute_en,
    z_score_interpretation,
    LILLIEFORS_CRITICAL_05,
    MANDEL_H_TABLE_05,
    MANDEL_H_TABLE_01,
)


class MockForm:
    def __init__(self, lab_code):
        self.laboratory = type("Lab", (), {"code": lab_code})()


class MockSubmission:
    def __init__(self, value, lab_code):
        self.value = value
        self.form = MockForm(lab_code)


class TestISO13528Statistics(unittest.TestCase):
    """Tests unitaires métrologiques conformes à l'ISO 13528 et ISO 5725-2."""

    def test_algorithm_a_robust_stats(self):
        """Vérifie la convergence et la robustesse de l'Algorithme A (ISO 13528 Annexe C)."""
        data = [10.2, 10.1, 10.3, 10.0, 10.2, 10.1, 10.4, 10.2, 15.0]  # avec une valeur atypique
        mean_star, s_star = robust_stats(data)
        self.assertIsNotNone(mean_star)
        self.assertIsNotNone(s_star)
        # La moyenne robuste doit être proche de 10.2 et insensible à la valeur 15.0
        self.assertAlmostEqual(mean_star, 10.2, delta=0.25)
        self.assertLess(s_star, 1.0)
        # La moyenne arithmétique classique non robuste est déformée par le 15.0
        arith_mean = sum(data) / len(data)
        self.assertGreater(arith_mean, 10.5)

    def test_grubbs_single_outlier(self):
        """Vérifie la détection d'une valeur aberrante unique (test de Grubbs)."""
        data = [10.0, 10.2, 10.1, 9.9, 10.3, 10.1, 10.0, 25.0]  # 25.0 est aberrante
        res = grubbs_test(data)
        self.assertIsNotNone(res["G"])
        self.assertIn("aberrante", res["status"].lower())
        self.assertEqual(res["outlier_value"], 25.0)

    def test_grubbs_double_outliers(self):
        """Vérifie la détection de deux valeurs aberrantes simultanées."""
        data = [10.0, 10.1, 10.2, 10.0, 10.1, 10.3, 28.0, 30.0]
        res = grubbs_test(data)
        self.assertIsNotNone(res.get("double_grubbs"))
        double_g = res["double_grubbs"]
        self.assertIn("aberrante", double_g.get("status", "").lower())
        self.assertEqual(double_g.get("pair_values"), [28.0, 30.0])

    def test_mandel_h_exact_thresholds(self):
        """Vérifie que les seuils critiques de Mandel h proviennent de la table ISO 5725-2."""
        # Pour p = 5 laboratoires
        self.assertEqual(MANDEL_H_TABLE_05[5], 1.57)
        self.assertEqual(MANDEL_H_TABLE_01[5], 1.72)
        # Pour p = 10 laboratoires
        self.assertEqual(MANDEL_H_TABLE_05[10], 1.80)
        self.assertEqual(MANDEL_H_TABLE_01[10], 2.18)

        # Simulation de 5 laboratoires dont un avec biais
        subs = [
            MockSubmission(10.0, "LAB1"),
            MockSubmission(10.1, "LAB2"),
            MockSubmission(9.9, "LAB3"),
            MockSubmission(10.0, "LAB4"),
            MockSubmission(13.5, "LAB5"),  # biais positif fort
        ]
        res = mandel_h_statistic(subs)
        self.assertEqual(res["n"], 5)
        self.assertEqual(res["h_critical_5"], 1.57)
        self.assertEqual(res["h_critical_1"], 1.72)
        self.assertTrue(res["has_outliers"] or res["has_stragglers"])
        lab5_res = [r for r in res["results"] if r["lab_code"] == "LAB5"][0]
        self.assertGreater(lab5_res["h"], 1.57)

    def test_lilliefors_normality_test(self):
        """Vérifie que le test de normalité applique les seuils Lilliefors corrigés."""
        # Seuil Lilliefors pour n=10 doit être 0.258 (bien inférieur au KS standard 0.410)
        self.assertEqual(LILLIEFORS_CRITICAL_05[10], 0.258)

        # Échantillon normal généré
        normal_data = [10.0, 10.2, 9.8, 10.1, 9.9, 10.3, 9.7, 10.0, 10.1, 10.0]
        res = kolmogorov_smirnov_test(normal_data)
        self.assertTrue(res["is_normal"])
        self.assertEqual(res["d_critical"], 0.258)
        self.assertIn("Lilliefors", res["test_name"])

    def test_cohen_fleiss_kappa(self):
        """Vérifie le calcul de l'indice Kappa pour les essais qualitatifs."""
        # Accord parfait (100% même résultat)
        perfect = ["Positif", "Positif", "Positif", "Positif", "Positif"]
        k_perf = compute_cohen_kappa(perfect)
        self.assertEqual(k_perf["kappa"], 1.0)
        self.assertEqual(k_perf["interpretation"], "Accord quasi-parfait")

        # Accord élevé
        high_agreement = ["Conforme"] * 9 + ["Non conforme"]
        k_high = compute_cohen_kappa(high_agreement)
        self.assertIsNotNone(k_high["kappa"])
        self.assertGreater(k_high["p_observed"], 0.8)

    def test_horwitz_rsd(self):
        """Vérifie l'équation de Horwitz pour les concentrations."""
        rsd = horwitz_rsd(100.0)
        self.assertIsNotNone(rsd)
        self.assertGreater(rsd, 0.0)

    def test_scores_z_and_en(self):
        """Vérifie le calcul des z-scores et du score En avec incertitude élargie."""
        z, is_anomaly = compute_z_score(12.0, 10.0, 1.0)
        self.assertEqual(z, 2.0)
        self.assertFalse(is_anomaly)
        interp, _ = z_score_interpretation(z)
        self.assertEqual(interp, "Satisfaisant")

        # compute_en(value, lab_U, target_value, target_U) -> (en, is_ok)
        en, is_ok = compute_en(10.4, 0.4, 10.0, 0.3)
        self.assertIsNotNone(en)
        # sqrt(0.4^2 + 0.3^2) = 0.5 -> En = 0.5 / 0.5 = 1.0
        self.assertAlmostEqual(en, 0.8, places=2)
        self.assertTrue(is_ok)


if __name__ == "__main__":
    unittest.main()
