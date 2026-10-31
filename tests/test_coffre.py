"""Coffre et sauvegarde, avec un faux Drive : aller-retour, mauvaise clé, aucun secret en clair."""

import json
import re
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

import outils.coffre as coffre
import outils.sauvegarde as sauvegarde

SECRET = "mot-de-passe-tres-secret-42"


class FauxDrive:
    """Les seuls appels du coffre : lister par nom, créer, remplacer, télécharger."""

    def __init__(self):
        self.fichiers, self.noms = {}, {}

    def get(self, url, params=None):
        if params.get("alt") == "media":
            return SimpleNamespace(content=self.fichiers[url.rsplit("/", 1)[1]], raise_for_status=lambda: None)
        nom = re.search(r"name = '([^']+)'", params["q"])[1]
        trouves = [{"id": i} for i, n in self.noms.items() if n == nom]
        return SimpleNamespace(json=lambda: {"files": trouves}, raise_for_status=lambda: None)

    def post(self, url, files=None):
        i = f"id{len(self.fichiers)}"
        self.noms[i], self.fichiers[i] = json.loads(files["metadata"][1])["name"], files["file"][1]
        return SimpleNamespace(raise_for_status=lambda: None)

    def patch(self, url, params=None, data=None, headers=None):
        self.fichiers[url.rsplit("/", 1)[1]] = data
        return SimpleNamespace(raise_for_status=lambda: None)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """Un dépôt installé : secrets, .env, profil, un dossier d'offre, le tout dans un dossier temporaire."""
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "token_google.json").write_text(json.dumps({"refresh_token": "jeton-refresh"}))
    (tmp_path / "secrets" / "comptes.json").write_text(json.dumps({"site.fr": {"email": "a@b", "mot_de_passe": SECRET}}))
    (tmp_path / "secrets" / ".oauth_en_attente.json").write_text("{}")
    (tmp_path / ".env").write_text(f"MDP_SITE={SECRET}\nVALIDATION_AUTO=1\n")
    (tmp_path / "profil").mkdir()
    (tmp_path / "profil" / "verite.md").write_text("Adresse confidentielle")
    (tmp_path / "dossiers" / "OF-1").mkdir(parents=True)
    (tmp_path / "dossiers" / "OF-1" / "CV.pdf").write_text("pdf")
    (tmp_path / "dossiers" / "OF-1" / "avant_envoi.png").write_text("capture")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "couts.csv").write_text("date")
    (tmp_path / "logs" / "journal_prive.log").write_text("à ne pas sauvegarder")
    for module, attribut, valeur in ((coffre, "RACINE", tmp_path), (coffre, "SECRETS", tmp_path / "secrets"),
                                     (coffre, "ENV", tmp_path / ".env"), (sauvegarde, "RACINE", tmp_path)):
        monkeypatch.setattr(module, attribut, valeur)
    monkeypatch.delenv("COFFRE_CLE", raising=False)
    drive = FauxDrive()
    monkeypatch.setattr(coffre, "session_drive", lambda: drive)
    monkeypatch.setattr(sauvegarde, "session_drive", lambda: drive)
    monkeypatch.setattr("outils.drive._dossier_racine", lambda session: "dossier")
    coffre.creer_cle()
    return SimpleNamespace(racine=tmp_path, drive=drive)


def effacer(racine):
    """Machine cloud neuve : seul le code reste ; COFFRE_CLE vient des réglages de l'environnement."""
    import shutil
    for nom in ("secrets", "profil", "dossiers", "logs", ".env"):
        cible = racine / nom
        shutil.rmtree(cible) if cible.is_dir() else cible.unlink()


def test_aller_retour_chiffrer_dechiffrer():
    cle = Fernet.generate_key().decode()
    assert coffre.dechiffrer(coffre.chiffrer(b"bonjour", cle), cle) == b"bonjour"


def test_mauvaise_cle_donne_une_erreur_claire():
    chiffre = coffre.chiffrer(b"x", Fernet.generate_key().decode())
    with pytest.raises(coffre.CoffreErreur, match="COFFRE_CLE ne correspond pas"):
        coffre.dechiffrer(chiffre, Fernet.generate_key().decode())


def test_cle_absente_ou_illisible_donne_une_erreur_claire(tmp_path, monkeypatch):
    monkeypatch.setattr(coffre, "ENV", tmp_path / ".env")
    monkeypatch.delenv("COFFRE_CLE", raising=False)
    with pytest.raises(coffre.CoffreErreur, match="COFFRE_CLE absente"):
        coffre.lire_cle()
    monkeypatch.setenv("COFFRE_CLE", "pas-une-cle")
    with pytest.raises(coffre.CoffreErreur, match="COFFRE_CLE illisible"):
        coffre.lire_cle()


def test_la_cle_porte_le_jeton_et_reste_la_meme_apres_nouvelle_autorisation(machine):
    cle_1, jeton_1 = coffre.lire_cle()
    assert jeton_1 == "jeton-refresh"
    (machine.racine / "secrets" / "token_google.json").write_text(json.dumps({"refresh_token": "autre"}))
    coffre.creer_cle()
    assert coffre.lire_cle() == (cle_1, "autre")


def test_rien_en_clair_sur_le_drive(machine):
    sauvegarde.sauvegarder()
    assert set(machine.drive.noms.values()) == {"coffre.bin", "archive.bin"}
    for contenu in machine.drive.fichiers.values():
        for clair in (SECRET, "Adresse confidentielle", "jeton-refresh"):
            assert clair.encode() not in contenu


def test_archive_sans_secret_ni_capture(machine):
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(sauvegarde.emballer_archive())) as archive:
        noms = archive.getnames()
        contenu = b"".join(archive.extractfile(m).read() for m in archive.getmembers() if m.isfile())
    assert "profil/verite.md" in noms and "dossiers/OF-1/CV.pdf" in noms and "logs/couts.csv" in noms
    assert not [n for n in noms if n.startswith("secrets") or n == ".env" or n.endswith(".png") or "journal_prive" in n]
    assert SECRET.encode() not in contenu


def test_le_coffre_ne_garde_ni_la_cle_ni_l_etat_oauth(machine):
    import io
    import tarfile
    with tarfile.open(fileobj=io.BytesIO(coffre.emballer_secrets())) as archive:
        noms = archive.getnames()
        env = archive.extractfile(".env").read().decode()
    assert ".env" in noms and "secrets/comptes.json" in noms and "secrets/.oauth_en_attente.json" not in noms
    assert "COFFRE_CLE" not in env and f"MDP_SITE={SECRET}" in env


def test_machine_neuve_retrouve_tout_et_message_exact(machine, capsys, monkeypatch):
    sauvegarde.sauvegarder()
    cle = coffre._brut()
    effacer(machine.racine)
    monkeypatch.setenv("COFFRE_CLE", cle)
    sauvegarde.restaurer()
    sortie = capsys.readouterr().out
    assert json.loads((machine.racine / "secrets" / "comptes.json").read_text())["site.fr"]["mot_de_passe"] == SECRET
    assert (machine.racine / "profil" / "verite.md").read_text() == "Adresse confidentielle"
    assert f"MDP_SITE={SECRET}" in (machine.racine / ".env").read_text()
    assert "accès Google et comptes (2 fichiers)" in sortie and "profil (1 fichier)" in sortie
    assert "projets" not in sortie   # rien à restaurer pour les projets : le message ne l'annonce pas
    assert (machine.racine / "secrets" / "comptes.json").stat().st_mode & 0o077 == 0


def test_jeton_expire_dit_quoi_faire():
    from google.auth.exceptions import RefreshError
    assert "autoriser à nouveau Google" in coffre.message_erreur(RefreshError("invalid_grant"))


def test_restaurer_sans_coffre_echoue_clairement(machine):
    with pytest.raises(coffre.CoffreErreur, match="Aucun coffre.bin"):
        sauvegarde.restaurer()


def test_compte_cree_sauvegarde_tout_de_suite(tmp_path, monkeypatch):
    import agents.postuleur as postuleur
    monkeypatch.setattr(postuleur, "COMPTES", tmp_path / "comptes.json")
    monkeypatch.setattr(postuleur, "RACINE", tmp_path)
    appels = []
    monkeypatch.setattr(coffre, "sauvegarder_coffre", lambda: appels.append(1))
    assert postuleur.identifiants("portail.exemple.fr")["mot_de_passe"]
    assert appels == [1]
    postuleur.identifiants("portail.exemple.fr")   # compte déjà connu : rien de neuf à sauvegarder
    assert appels == [1]
    postuleur.identifiants("portail.exemple.fr", "mot-de-passe-retrouve")
    assert appels == [1, 1]


def test_coffre_indisponible_ne_perd_pas_le_compte(tmp_path, monkeypatch, capsys):
    import agents.postuleur as postuleur
    monkeypatch.setattr(postuleur, "COMPTES", tmp_path / "comptes.json")
    monkeypatch.setattr(postuleur, "RACINE", tmp_path)

    def en_panne():
        raise coffre.CoffreErreur("Drive indisponible")
    monkeypatch.setattr(coffre, "sauvegarder_coffre", en_panne)
    assert postuleur.identifiants("portail2.exemple.fr")["mot_de_passe"]
    assert "ATTENTION" in capsys.readouterr().out and (tmp_path / "comptes.json").exists()


def test_journal_masque_les_mots_de_passe(monkeypatch):
    from outils.lancement import _entree_sans_secret, _masquer
    assert SECRET not in _entree_sans_secret({"site": "x", "mot_de_passe": SECRET})
    monkeypatch.setenv("COFFRE_CLE", "valeur-de-la-cle")
    monkeypatch.setenv("MDP_SITE", SECRET)
    assert _masquer(f"a valeur-de-la-cle b {SECRET}") == "a *** b ***"
