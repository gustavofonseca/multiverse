from typing import Callable, Dict, List, Any
import difflib
from io import BytesIO
from enum import Enum, auto

from clea import join as clea_join, core as clea_core

from .interfaces import Session
from .domain import Document, DocumentsBundle, Journal

__all__ = ["get_handlers"]


class Events(Enum):
    """Eventos emitidos por instâncias de `CommandHandler`.
    """

    DOCUMENT_REGISTERED = auto()
    DOCUMENT_VERSION_REGISTERED = auto()
    ASSET_VERSION_REGISTERED = auto()
    DOCUMENTSBUNDLE_CREATED = auto()
    DOCUMENTSBUNDLE_METATADA_UPDATED = auto()
    DOCUMENT_ADDED_TO_DOCUMENTSBUNDLE = auto()
    DOCUMENT_INSERTED_TO_DOCUMENTSBUNDLE = auto()
    JOURNAL_CREATED = auto()
    ISSUE_ADDED_TO_JOURNAL = auto()


class CommandHandler:
    def __init__(self, Session: Callable[[], Session]):
        self.Session = Session


class BaseRegisterDocument(CommandHandler):
    """Implementação abstrata de comando para registrar um novo documento.

    :param id: Identificador alfanumérico para o documento. Deve ser único.
    :param data_url: URL válida e publicamente acessível para o documento em XML 
    SciELO PS.
    """

    def _get_document(self, session: Session, id: str) -> Document:
        raise NotImplementedError()

    def _persist(self, session: Session, document: Document) -> None:
        raise NotImplementedError()

    def _notify(self, session: Session, data) -> None:
        raise NotImplementedError()

    def __call__(self, id: str, data_url: str, assets: Dict[str, str] = None) -> None:
        try:
            assets = dict(assets)
        except TypeError:
            assets = {}
        session = self.Session()
        document = self._get_document(session, id)
        document.new_version(data_url)
        for asset_id, asset_url in assets.items():
            document.new_asset_version(asset_id, asset_url)
        self._persist(session, document)
        self._notify(
            session,
            {"document": document, "id": id, "data_url": data_url, "assets": assets},
        )


class RegisterDocument(BaseRegisterDocument):
    """Registra um novo documento.

    :param id: Identificador alfanumérico para o documento. Deve ser único.
    :param data_url: URL válida e publicamente acessível para o documento em XML 
    SciELO PS.
    """

    def _get_document(self, session, id):
        return Document(id=id)

    def _persist(self, session, document):
        return session.documents.add(document)

    def _notify(self, session, data):
        session.notify(Events.DOCUMENT_REGISTERED, data)


class RegisterDocumentVersion(BaseRegisterDocument):
    """Registra uma nova versão de um documento já registrado.

    :param id: Identificador alfanumérico para o documento.
    :param data_url: URL válida e publicamente acessível para o documento em XML 
    SciELO PS.
    """

    def _get_document(self, session, id):
        return session.documents.fetch(id)

    def _persist(self, session, document):
        return session.documents.update(document)

    def _notify(self, session, data):
        session.notify(Events.DOCUMENT_VERSION_REGISTERED, data)


class FetchDocumentData(CommandHandler):
    """Recupera o documento em XML à partir de seu identificador.

    :param id: Identificador único do documento.
    :param version_index: (opcional) Número inteiro correspondente a versão do 
    documento. Por padrão retorna a versão mais recente.
    :param version_at: (opcional) string de texto de um timestamp UTC
    referente a versão do documento no determinado momento. O uso do argumento
    `version_at` faz com que qualquer valor de `version_index` seja ignorado.
    """

    def __call__(
        self, id: str, version_index: int = -1, version_at: str = None
    ) -> bytes:
        session = self.Session()
        document = session.documents.fetch(id)
        return document.data(version_index=version_index, version_at=version_at)


class FetchDocumentManifest(CommandHandler):
    """Recupera o manifesto do documento à partir de seu identificador.

    :param id: Identificador único do documento.
    """

    def __call__(self, id: str) -> dict:
        session = self.Session()
        document = session.documents.fetch(id)
        return document.manifest


class FetchAssetsList(CommandHandler):
    """Recupera a lista de ativos do documento à partir de seu identificador.

    :param id: Identificador único do documento.
    :param version_index: (opcional) Número inteiro correspondente a versão do 
    documento. Por padrão retorna a versão mais recente.
    """

    def __call__(self, id: str, version_index: int = -1) -> dict:
        session = self.Session()
        document = session.documents.fetch(id)
        return document.version(index=version_index)


class RegisterAssetVersion(BaseRegisterDocument):
    """Registra uma nova versão do ativo digital de documento já registrado.

    :param id: Identificador alfanumérico para o documento.
    :param asset_id: Identificador alfanumérico para o ativo.
    :param asset_url: URL válida e publicamente acessível para o ativo digital.
    """

    def __call__(self, id: str, asset_id: str, asset_url: str) -> None:
        session = self.Session()
        document = session.documents.fetch(id)
        document.new_asset_version(asset_id=asset_id, data_url=asset_url)
        result = session.documents.update(document)
        session.notify(
            Events.ASSET_VERSION_REGISTERED,
            {
                "document": document,
                "id": id,
                "asset_id": asset_id,
                "asset_url": asset_url,
            },
        )
        return result


class DiffDocumentVersions(CommandHandler):
    """Compara duas versões do Documento.

    :param id: Identificador único do documento.
    :param from_version_at: string de texto de um timestamp UTC referente a 
    versão do documento que será a base da comparação.
    :param to_version_at: (opcional) string de texto de um timestamp UTC 
    referente a versão final do documento a ser comparada. Se não for informada 
    será utilizada a versão mais recente.
    """

    def __call__(
        self, id: str, from_version_at: str, to_version_at: str = None
    ) -> bytes:
        session = self.Session()
        document = session.documents.fetch(id)
        from_version = document.data(version_at=from_version_at).splitlines()
        if to_version_at:
            _to_version_at = {"version_at": to_version_at}
        else:
            _to_version_at = {}
        to_version = document.data(**_to_version_at).splitlines()
        diff = difflib.diff_bytes(
            difflib.unified_diff,
            from_version,
            to_version,
            fromfile=from_version_at.encode("utf-8"),
            tofile=to_version_at.encode("utf-8") if to_version_at else b"latest",
            lineterm=b"",
        )
        return b"\n".join(diff)


class SanitizeDocumentFront(CommandHandler):
    """Sanitiza o front-matter do documento.

    :param xml_data: string de bytes do conteúdo do documento em XML.
    """

    def __call__(self, xml_data: bytes) -> dict:
        clea_article = clea_core.Article(BytesIO(xml_data))
        front_data = {
            tag_name: [branch.data for branch in clea_article.get(tag_name)]
            for tag_name in ["journal-meta", "article-meta"]
        }
        front_data["contrib"] = clea_join.aff_contrib_inner_join(clea_article)
        return self._rearrange(front_data)

    def _rearrange(self, data):
        def _first(iterable, default=""):
            try:
                return next(iter(iterable))
            except StopIteration:
                return default

        _data = {
            "journal-meta": _first(data["journal-meta"], {}),
            "article-meta": _first(data["article-meta"], {}),
        }
        _data["contrib"] = [
            {
                k: v
                for k, v in contrib.items()
                if k not in _data["journal-meta"] and k not in _data["article-meta"]
            }
            for contrib in data["contrib"]
        ]
        return _data


class CreateDocumentsBundle(CommandHandler):
    def __call__(self, id: str, docs: list = None, metadata: dict = None) -> None:
        session = self.Session()
        _bundle = DocumentsBundle(id)
        for doc in docs or []:
            _bundle.add_document(doc)
        for name, value in (metadata or {}).items():
            setattr(_bundle, name, value)
        result = session.documents_bundles.add(_bundle)
        session.notify(
            Events.DOCUMENTSBUNDLE_CREATED,
            {"bundle": _bundle, "id": id, "docs": docs, "metadata": metadata},
        )
        return result


class FetchDocumentsBundle(CommandHandler):
    def __call__(self, id: str) -> dict:
        session = self.Session()
        return session.documents_bundles.fetch(id).manifest


class UpdateDocumentsBundleMetadata(CommandHandler):
    def __call__(self, id: str, metadata: dict) -> None:
        session = self.Session()
        _bundle = session.documents_bundles.fetch(id)
        for name, value in metadata.items():
            setattr(_bundle, name, value)
        session.documents_bundles.update(_bundle)
        session.notify(
            Events.DOCUMENTSBUNDLE_METATADA_UPDATED,
            {"bundle": _bundle, "id": id, "metadata": metadata},
        )


class AddDocumentToDocumentsBundle(CommandHandler):
    def __call__(self, id: str, doc: str) -> None:
        session = self.Session()
        _bundle = session.documents_bundles.fetch(id)
        _bundle.add_document(doc)
        session.documents_bundles.update(_bundle)
        session.notify(
            Events.DOCUMENT_ADDED_TO_DOCUMENTSBUNDLE,
            {"bundle": _bundle, "id": id, "doc": doc},
        )


class InsertDocumentToDocumentsBundle(CommandHandler):
    def __call__(self, id: str, index: int, doc: str) -> None:
        session = self.Session()
        _bundle = session.documents_bundles.fetch(id)
        _bundle.insert_document(index, doc)
        session.documents_bundles.update(_bundle)
        session.notify(
            Events.DOCUMENT_INSERTED_TO_DOCUMENTSBUNDLE,
            {"bundle": _bundle, "id": id, "index": index, "doc": doc},
        )


class CreateJournal(CommandHandler):
    def __call__(self, id: str, metadata: Dict[str, Any] = None) -> None:
        session = self.Session()
        _journal = Journal(id)
        for name, value in (metadata or {}).items():
            setattr(_journal, name, value)
        result = session.journals.add(_journal)
        session.notify(
            Events.JOURNAL_CREATED,
            {"journal": _journal, "id": id, "metadata": metadata},
        )
        return result


class AddIssueToJournal(CommandHandler):
    def __call__(self, id: str, issue: str) -> None:
        session = self.Session()
        _journal = session.journals.fetch(id)
        _journal.add_issue(issue)
        session.journals.update(_journal)
        session.notify(
            Events.ISSUE_ADDED_TO_JOURNAL,
            {"journal": _journal, "id": "0034-8910-rsp", "issue": "0034-8910-rsp-48-2"},
        )


DEFAULT_SUBSCRIBERS = []


def get_handlers(
    Session: Callable[[], Session], subscribers=DEFAULT_SUBSCRIBERS
) -> dict:
    """Ponto de acesso aos serviços do Kernel.

    :param Session: factory de instâncias de interfaces.Session.
    :param subscribers (opcional): mapeamento entre eventos e callbacks, na
    forma de lista associativa.
    """

    def SessionWrapper():
        """Produz instância de `Session` inicializada com seus observadores. 
        """
        session = Session()
        for event, callback in subscribers:
            session.observe(event, callback)
        return session

    return {
        "register_document": RegisterDocument(SessionWrapper),
        "register_document_version": RegisterDocumentVersion(SessionWrapper),
        "fetch_document_data": FetchDocumentData(SessionWrapper),
        "fetch_document_manifest": FetchDocumentManifest(SessionWrapper),
        "fetch_assets_list": FetchAssetsList(SessionWrapper),
        "register_asset_version": RegisterAssetVersion(SessionWrapper),
        "diff_document_versions": DiffDocumentVersions(SessionWrapper),
        "sanitize_document_front": SanitizeDocumentFront(SessionWrapper),
        "create_documents_bundle": CreateDocumentsBundle(SessionWrapper),
        "fetch_documents_bundle": FetchDocumentsBundle(SessionWrapper),
        "update_documents_bundle_metadata": UpdateDocumentsBundleMetadata(
            SessionWrapper
        ),
        "add_document_to_documents_bundle": AddDocumentToDocumentsBundle(
            SessionWrapper
        ),
        "insert_document_to_documents_bundle": InsertDocumentToDocumentsBundle(
            SessionWrapper
        ),
        "create_journal": CreateJournal(SessionWrapper),
        "add_issue_to_journal": AddIssueToJournal(SessionWrapper),
    }
