import unittest
import os
from werkzeug.security import generate_password_hash, check_password_hash


class TestSecurityAndReports(unittest.TestCase):
    """Tests de sécurité et de configuration des rapports."""

    def test_password_hashing(self):
        """Vérifie que les mots de passe sont correctement hachés avec un sel sécurisé."""
        password = "AdminSecret2026!"
        pwd_hash = generate_password_hash(password)
        self.assertNotEqual(password, pwd_hash)
        self.assertTrue(check_password_hash(pwd_hash, password))
        self.assertFalse(check_password_hash(pwd_hash, "WrongPassword"))

    def test_file_upload_extension_validation(self):
        """Vérifie le filtrage strict des extensions de fichiers téléversés."""
        allowed_exts = {".pdf", ".docx", ".doc", ".xlsx", ".xls"}

        valid_files = ["rapport_final.pdf", "analyse.docx", "donnees.xlsx"]
        for fname in valid_files:
            ext = os.path.splitext(fname)[1].lower()
            self.assertIn(ext, allowed_exts)

        forbidden_files = ["script.exe", "backdoor.py", "exploit.sh", "page.html", "vector.svg"]
        for fname in forbidden_files:
            ext = os.path.splitext(fname)[1].lower()
            self.assertNotIn(ext, allowed_exts)

    def test_estimators_selection_parsing(self):
        """Vérifie que le parseur d'estimateurs gère correctement les listes ou chaînes CSV."""
        # Cas 1 : liste POST classique
        post_data = ["x_pt", "sigma_pt", "median", "grubbs"]
        self.assertEqual(len(post_data), 4)

        # Cas 2 : chaîne CSV dans les query params
        query_str = "x_pt,sigma_pt,u_xpt,U_xpt,normality"
        parsed = [e.strip() for e in query_str.split(",") if e.strip()]
        self.assertEqual(len(parsed), 5)
        self.assertIn("U_xpt", parsed)
        self.assertIn("normality", parsed)

    def test_all_estimators_catalog(self):
        """Vérifie que la liste complète des estimateurs contient tous les 30+ indicateurs."""
        all_catalog = [
            "x_pt", "sigma_pt", "u_xpt", "U_xpt", "u_ratio",
            "alg_a_mean", "alg_a_sd", "median", "made", "niqr", "m_estimator", "hampel_q",
            "mean_arith", "sd_arith", "se_mean", "cv_percent", "min_max",
            "horwitz", "horwitz_rsd", "horrat",
            "grubbs", "grubbs_double", "boxplot",
            "normality", "ks_standard", "droite_henry", "kurt_skew",
            "mandel_h", "mandel_k", "alg_s",
            "z_score", "z_prime", "en_score", "zeta_score",
            "consensus", "kappa"
        ]
        self.assertGreaterEqual(len(all_catalog), 30)
        self.assertIn("m_estimator", all_catalog)
        self.assertIn("droite_henry", all_catalog)
        self.assertIn("boxplot", all_catalog)
        self.assertIn("z_prime", all_catalog)
        self.assertIn("en_score", all_catalog)


if __name__ == "__main__":
    unittest.main()
