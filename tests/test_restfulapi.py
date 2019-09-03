import os
import unittest
from copy import deepcopy
from unittest.mock import patch, Mock

import colander
from pyramid import testing
from pyramid.httpexceptions import (
    HTTPOk,
    HTTPNotFound,
    HTTPCreated,
    HTTPNoContent,
    HTTPBadRequest,
    HTTPUnprocessableEntity,
)

from documentstore import services, restfulapi, exceptions
from . import apptesting

_CWD = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_CWD, "0034-8910-rsp-48-2-0347.xml"), "rb") as f:
    SAMPLE_DOCUMENT_DATA = f.read()


def make_request():
    request = testing.DummyRequest()
    session = apptesting.Session()
    request.services = services.get_handlers(lambda: session)
    return request


def fetch_data_stub(url, timeout=2):
    assert url.endswith("0034-8910-rsp-48-2-0347.xml")
    return SAMPLE_DOCUMENT_DATA


@patch("documentstore.domain.fetch_data", new=fetch_data_stub)
class FetchDocumentDataUnitTests(unittest.TestCase):
    def test_when_doesnt_exist_returns_http_404(self):
        request = make_request()
        request.matchdict = {"document_id": "unknown"}
        self.assertRaises(HTTPNotFound, restfulapi.fetch_document_data, request)

    def test_when_exists_returns_xml_as_bytes(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.services["register_document"](
            id="my-testing-doc",
            data_url="https://raw.githubusercontent.com/scieloorg/packtools/master/tests/samples/0034-8910-rsp-48-2-0347.xml",
            assets={},
        )

        document_data = restfulapi.fetch_document_data(request)
        self.assertIsInstance(document_data, bytes)

    def test_versions_prior_to_creation_returns_http_404(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.GET = {"when": "1900-01-01"}
        request.services["register_document"](
            id="my-testing-doc",
            data_url="https://raw.githubusercontent.com/scieloorg/packtools/master/tests/samples/0034-8910-rsp-48-2-0347.xml",
            assets={},
        )
        self.assertRaises(HTTPNotFound, restfulapi.fetch_document_data, request)

    def test_versions_in_distant_future_returns_xml_as_bytes(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.GET = {"when": "2100-01-01"}
        request.services["register_document"](
            id="my-testing-doc",
            data_url="https://raw.githubusercontent.com/scieloorg/packtools/master/tests/samples/0034-8910-rsp-48-2-0347.xml",
            assets={},
        )

        document_data = restfulapi.fetch_document_data(request)
        self.assertIsInstance(document_data, bytes)


@patch("documentstore.domain.fetch_data", new=fetch_data_stub)
class PutDocumentUnitTests(unittest.TestCase):
    def test_registration_of_new_document_returns_201(self):
        request = make_request()
        request.matchdict = {"document_id": "0034-8910-rsp-48-2-0347"}
        request.validated = apptesting.document_registry_data_fixture()
        self.assertIsInstance(restfulapi.put_document(request), HTTPCreated)

    def test_registration_of_update_returns_204(self):
        request = make_request()
        request.matchdict = {"document_id": "0034-8910-rsp-48-2-0347"}
        request.validated = apptesting.document_registry_data_fixture()
        restfulapi.put_document(request)

        request.matchdict = {"document_id": "0034-8910-rsp-48-2-0347"}
        request.validated = apptesting.document_registry_data_fixture(prefix="v2-")
        self.assertIsInstance(restfulapi.put_document(request), HTTPNoContent)

    def test_registration_of_update_is_idempotent_and_returns_204(self):
        request = make_request()
        request.matchdict = {"document_id": "0034-8910-rsp-48-2-0347"}
        request.validated = apptesting.document_registry_data_fixture()
        restfulapi.put_document(request)
        self.assertIsInstance(restfulapi.put_document(request), HTTPNoContent)


class ParseSettingsFunctionTests(unittest.TestCase):
    def test_known_values_are_preserved_when_given(self):
        defaults = [("apptest.foo", "APPTEST_FOO", str, "modified foo")]
        self.assertEqual(
            restfulapi.parse_settings(
                {"apptest.foo": "original foo"}, defaults=defaults
            ),
            {"apptest.foo": "original foo"},
        )

    def test_use_default_when_value_is_missing(self):
        defaults = [("apptest.foo", "APPTEST_FOO", str, "foo value")]
        self.assertEqual(
            restfulapi.parse_settings({}, defaults=defaults),
            {"apptest.foo": "foo value"},
        )

    def test_env_vars_have_precedence_over_given_values(self):
        try:
            os.environ["APPTEST_FOO"] = "foo from env"

            defaults = [("apptest.foo", "APPTEST_FOO", str, "foo value")]
            self.assertEqual(
                restfulapi.parse_settings({}, defaults=defaults),
                {"apptest.foo": "foo from env"},
            )
        finally:
            os.environ.pop("APPTEST_FOO", None)

    def test_known_values_always_have_their_types_converted(self):
        defaults = [("apptest.foo", "APPTEST_FOO", int, "42")]
        self.assertEqual(
            restfulapi.parse_settings({}, defaults=defaults), {"apptest.foo": 42}
        )
        self.assertEqual(
            restfulapi.parse_settings({"apptest.foo": "17"}, defaults=defaults),
            {"apptest.foo": 17},
        )
        try:
            os.environ["APPTEST_FOO"] = "13"

            self.assertEqual(
                restfulapi.parse_settings({}, defaults=defaults), {"apptest.foo": 13}
            )
        finally:
            os.environ.pop("APPTEST_FOO", None)


class FetchDocumentsBundleTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("bundles", pattern="/bundles/{bundle_id}")

    def test_fetch_documents_bundle_raises_bad_request_if_bundle_id_is_not_informed(
        self
    ):
        response = restfulapi.fetch_documents_bundle(self.request)
        self.assertIsInstance(response, HTTPBadRequest)
        self.assertEqual(response.message, "bundle id is mandatory")

    def test_fetch_documents_bundle_raises_not_found_if_bundle_does_not_exist(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        MockFetchDocumentsBundle = Mock(
            side_effect=exceptions.DoesNotExist("Does Not Exist")
        )
        self.request.services["fetch_documents_bundle"] = MockFetchDocumentsBundle
        response = restfulapi.fetch_documents_bundle(self.request)
        self.assertIsInstance(response, HTTPNotFound)
        self.assertEqual(response.message, "Does Not Exist")

    def test_fetch_documents_bundle_calls_fetch_documents_bundle_service(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        MockFetchDocumentsBundle = Mock(return_value={"id": "0034-8910-rsp-48-2"})
        self.request.services["fetch_documents_bundle"] = MockFetchDocumentsBundle
        restfulapi.fetch_documents_bundle(self.request)
        MockFetchDocumentsBundle.assert_called_once_with("0034-8910-rsp-48-2")

    def test_fetch_documents_bundle_returns_fetch_documents_bundle_service_return(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        expected = apptesting.documents_bundle_registry_data_fixture()
        data = deepcopy(expected)
        MockFetchDocumentsBundle = Mock(return_value=data)
        self.request.services["fetch_documents_bundle"] = MockFetchDocumentsBundle
        self.assertEqual(restfulapi.fetch_documents_bundle(self.request), expected)


class DocumentsBundleSchemaTest(unittest.TestCase):
    def test_none_of_fields_required(self):
        data = apptesting.documents_bundle_registry_data_fixture()
        for field_name in data.keys():
            data_2 = deepcopy(data)
            with self.subTest(field_name=field_name):
                del data_2[field_name]
                deserialized = restfulapi.DocumentsBundleSchema().deserialize(data_2)
                self.assertIsNone(deserialized.get(field_name))

    def test_check_titles_if_title_is_present(self):
        data = {}
        titles = (
            ["Invalid Title"],
            [{"a": 1, "b": 2}],
            [{"language": "en"}],
            [{"title": "Title"}],
        )
        for title in titles:
            with self.subTest(title=title):
                data["titles"] = title
                self.assertRaises(
                    colander.Invalid,
                    restfulapi.DocumentsBundleSchema().deserialize,
                    data,
                )

    def test_valid(self):
        data = apptesting.documents_bundle_registry_data_fixture()
        restfulapi.DocumentsBundleSchema().deserialize(data)

    def test_if_month_and_range_are_mutually_exclusive(self):
        data = apptesting.documents_bundle_registry_data_fixture()
        pub_months_dict = data['publication_months']
        pub_months_dict['range'] = (1, 2)
        data['publication_months'] = pub_months_dict

        self.assertRaises(
            colander.Invalid, restfulapi.JournalIssuesSchema().deserialize, data
        )


class PutDocumentsBundleTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("bundles", pattern="/bundles/{bundle_id}")

    def test_put_documents_bundle_calls_create_documents_bundle(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        self.request.validated = apptesting.documents_bundle_registry_data_fixture()
        expected = deepcopy(self.request.validated)
        MockCreateDocumentsBundle = Mock()
        self.request.services["create_documents_bundle"] = MockCreateDocumentsBundle
        restfulapi.put_documents_bundle(self.request)
        MockCreateDocumentsBundle.assert_called_once_with(
            "0034-8910-rsp-48-2", metadata=expected
        )

    def test_put_documents_bundle_returns_204_if_already_exists(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        self.request.validated = apptesting.documents_bundle_registry_data_fixture()
        MockCreateDocumentsBundle = Mock(
            side_effect=exceptions.AlreadyExists("Already Exists")
        )
        self.request.services["create_documents_bundle"] = MockCreateDocumentsBundle
        response = restfulapi.put_documents_bundle(self.request)
        self.assertIsInstance(response, HTTPNoContent)

    def test_put_documents_bundle_returns_201_if_created(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        self.request.validated = apptesting.documents_bundle_registry_data_fixture()
        self.request.services["create_documents_bundle"] = Mock()
        response = restfulapi.put_documents_bundle(self.request)
        self.assertIsInstance(response, HTTPCreated)


class PatchDocumentsBundleTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("bundles", pattern="/bundles/{bundle_id}")

    def test_patch_documents_bundle_return_404_if_no_bundle_found(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        self.request.validated = apptesting.documents_bundle_registry_data_fixture()
        MockUpdateDocumentsBundle = Mock(
            side_effect=exceptions.DoesNotExist("Does Not Exist")
        )
        self.request.services[
            "update_documents_bundle_metadata"
        ] = MockUpdateDocumentsBundle
        response = restfulapi.patch_documents_bundle(self.request)
        self.assertIsInstance(response, HTTPNotFound)

    def test_patch_documents_bundle_calls_update_documents_bundle(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        self.request.validated = apptesting.documents_bundle_registry_data_fixture()
        expected = deepcopy(self.request.validated)
        MockUpdateDocumentsBundle = Mock()
        self.request.services[
            "update_documents_bundle_metadata"
        ] = MockUpdateDocumentsBundle
        restfulapi.patch_documents_bundle(self.request)
        MockUpdateDocumentsBundle.assert_called_once_with(
            "0034-8910-rsp-48-2", metadata=expected
        )

    def test_put_documents_bundle_returns_204_if_updated(self):
        self.request.matchdict["bundle_id"] = "0034-8910-rsp-48-2"
        self.request.validated = apptesting.documents_bundle_registry_data_fixture()
        self.request.services["update_documents_bundle_metadata"] = Mock()
        response = restfulapi.patch_documents_bundle(self.request)
        self.assertIsInstance(response, HTTPNoContent)


class PutDocumentsBundleDocumentTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route(
            "bundles_documents", pattern="/bundles/{bundle_id}/documents"
        )

        # register a issue
        self.request.matchdict = {"bundle_id": "example-bundle-id"}
        self.request.validated = apptesting.documents_bundle_registry_data_fixture()
        restfulapi.put_documents_bundle(self.request)

    def test_should_call_update_documents_in_issues(self):
        self.request.matchdict["bundle_id"] = "example-bundle-id"
        self.request.validated = [{"id": "doc-1"}, {"id": "doc-2"}]
        MockUpdateDocumentsInIssues = Mock()
        self.request.services[
            "update_documents_in_documents_bundle"
        ] = MockUpdateDocumentsInIssues
        restfulapi.put_bundles_documents(self.request)
        MockUpdateDocumentsInIssues.assert_called_once_with(
            id="example-bundle-id", docs=[{"id": "doc-1"}, {"id": "doc-2"}]
        )

    def test_should_return_422_if_already_exists_exception_is_raised(self):
        self.request.matchdict["bundle_id"] = "example-bundle-id"
        self.request.validated = [{"id": "doc-1"}, {"id": "doc-1"}]
        response = restfulapi.put_bundles_documents(self.request)
        self.assertIsInstance(response, HTTPUnprocessableEntity)

    def test_should_not_update_if_already_exists_exception_is_raised(self):
        self.request.matchdict["bundle_id"] = "example-bundle-id"
        self.request.validated = [{"id": "doc-1"}, {"id": "doc-1"}]
        restfulapi.put_bundles_documents(self.request)
        response = restfulapi.fetch_documents_bundle(self.request)
        self.assertEqual([], response.get("items"))

    def test_should_return_404_if_bundle_not_found(self):
        self.request.matchdict["bundle_id"] = "example-bundle-id"
        self.request.validated = [{"id": "doc-1"}]
        MockUpdateDocumentsInIssues = Mock(
            side_effect=exceptions.DoesNotExist("Does Not Exist")
        )
        self.request.services[
            "update_documents_in_documents_bundle"
        ] = MockUpdateDocumentsInIssues
        response = restfulapi.put_bundles_documents(self.request)
        self.assertIsInstance(response, HTTPNotFound)

    def test_should_return_204_if_bundle_issues_was_updated(self):
        self.request.matchdict["bundle_id"] = "example-bundle-id"
        self.request.validated = [{"id": "doc-1"}]
        response = restfulapi.put_bundles_documents(self.request)
        self.assertIsInstance(response, HTTPNoContent)


class FetchChangeUnitTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("documents", pattern="/documents/{document_id}")

    def make_documents(self, quant):
        for i in range(quant):
            self.request.matchdict = {"document_id": f"0000-0000-23-24-223{i}"}
            self.request.validated = apptesting.document_registry_data_fixture()
            restfulapi.put_document(self.request)

    def test_fetch_changes(self):
        self.assertEqual(
            restfulapi.fetch_changes(self.request),
            {"since": "", "limit": 500, "results": []},
        )

    def test_limit_must_be_int(self):
        self.request.GET["limit"] = "foo"
        self.assertRaises(HTTPBadRequest, restfulapi.fetch_changes, self.request)

    def test_limit_return_correct_value(self):
        self.request.GET["limit"] = 1000
        self.assertEqual(restfulapi.fetch_changes(self.request)["limit"], 1000)

    def test_since_return_correct_value(self):
        self.request.GET["since"] = "2019-02-21T13:52:26.526904Z"
        self.assertEqual(
            restfulapi.fetch_changes(self.request)["since"],
            "2019-02-21T13:52:26.526904Z",
        )

    def test_document_inserted_reflects_in_changes(self):
        self.request.matchdict = {"document_id": "0000-0000-23-24-2231"}
        self.request.validated = apptesting.document_registry_data_fixture()
        restfulapi.put_document(self.request)
        self.assertEqual(len(restfulapi.fetch_changes(self.request)["results"]), 1)
        changes_ids = [
            change["id"] for change in restfulapi.fetch_changes(self.request)["results"]
        ]
        self.assertIn("/documents/0000-0000-23-24-2231", changes_ids)

    def test_since_filter_the_change_list(self):
        self.make_documents(10)
        since = restfulapi.fetch_changes(self.request)["results"][5]["timestamp"]
        self.request.GET["since"] = since

        self.assertEqual(len(restfulapi.fetch_changes(self.request)["results"]), 4)

    def test_since_must_return_empty_result_list_with_unknown_value(self):
        self.make_documents(5)
        self.request.GET["since"] = "xxx"

        self.assertEqual(len(restfulapi.fetch_changes(self.request)["results"]), 0)

    def test_fetch_with_since_and_limit(self):
        self.make_documents(20)
        changes = restfulapi.fetch_changes(self.request)["results"]
        since = changes[10]["timestamp"]

        self.request.GET["since"] = since
        self.request.GET["limit"] = 5

        self.assertEqual(
            restfulapi.fetch_changes(self.request)["results"], changes[11:16]
        )


class CreateJournalUnitTests(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("journals", pattern="/journals/{journals_id}")

    def test_should_return_created(self):
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = apptesting.journal_registry_fixture()
        self.assertIsInstance(restfulapi.put_journal(self.request), HTTPCreated)

    def test_should_return_no_concent_if_already_exists(self):
        MockCreateJournal = Mock(side_effect=exceptions.AlreadyExists)
        self.request.services["create_journal"] = MockCreateJournal
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = apptesting.journal_registry_fixture()
        self.assertIsInstance(restfulapi.put_journal(self.request), HTTPNoContent)

    def test_should_return_a_bad_request_if_domain_raise_an_exception(self):
        MockCreateJournal = Mock(side_effect=ValueError)
        self.request.services["create_journal"] = MockCreateJournal
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = apptesting.journal_registry_fixture(
            subject_areas=["invalid-subject-area"]
        )
        self.assertIsInstance(restfulapi.put_journal(self.request), HTTPBadRequest)


class FetchJournalUnitTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("journals", pattern="/journals/{journal_id}")

        # register a journal
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = apptesting.journal_registry_fixture()
        restfulapi.put_journal(self.request)

    def test_should_return_does_not_exists(self):
        MockFetchJournal = Mock(side_effect=exceptions.DoesNotExist)
        self.request.services["fetch_journal"] = MockFetchJournal
        self.request.matchdict = {"journal_id": "some-random-id-001"}
        self.assertIsInstance(restfulapi.get_journal(self.request), HTTPNotFound)

    def test_should_fetch_journal(self):
        self.request.services["fetch_journal"](id="1678-4596-cr-49-02")
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        journal_data = restfulapi.get_journal(self.request)
        self.assertIsInstance(journal_data, dict)


class PatchJournalUnitTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("journals", pattern="/journals/{journal_id}")

        # register a journal
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = apptesting.journal_registry_fixture()
        restfulapi.put_journal(self.request)

    def test_should_raise_exception_if_journal_does_not_exists(self):
        self.request.matchdict = {"journal_id": "some-random-id-001"}
        self.assertIsInstance(restfulapi.patch_journal(self.request), HTTPNotFound)

    def test_should_update_a_journal(self):
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = {"title": "Ciência Rural-2"}
        self.assertIsInstance(restfulapi.patch_journal(self.request), HTTPNoContent)

    def test_should_raise_value_error_exception(self):
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.services["update_journal_metadata"] = Mock(side_effect=ValueError)
        self.assertIsInstance(restfulapi.patch_journal(self.request), HTTPBadRequest)

        self.request.services["update_journal_metadata"] = Mock(side_effect=TypeError)
        self.assertIsInstance(restfulapi.patch_journal(self.request), HTTPBadRequest)


class JournalIssuesSchemaTest(unittest.TestCase):
    def test_index_field_is_optional(self):
        data = {"issue": {"id": "1678-4596-cr-25-3", "year": "2019"}}
        restfulapi.JournalIssuesSchema().deserialize(data)

    def test_issue_field_is_required(self):
        self.assertRaises(
            colander.Invalid, restfulapi.JournalIssuesSchema().deserialize, {"index": 0}
        )

    def test_check_fields_type(self):
        invalid_data = ({"issue": 1678, "index": ""}, {"issue": 1678})
        for data in invalid_data:
            with self.subTest(data=data):
                self.assertRaises(
                    colander.Invalid, restfulapi.JournalIssuesSchema().deserialize, data
                )

    def test_valid(self):
        data = {
            "issue": {
                "id": "1678-4596-cr-25-3",
                "year": "2019",
                "volume": "1",
                "number": "2",
            },
            "index": 10,
        }
        restfulapi.JournalIssuesSchema().deserialize(data)

    def test_year_should_be_required(self):
        data = {"issue": {"id": "1678-4596-cr-25-3"}}
        self.assertRaises(
            colander.Invalid, restfulapi.JournalIssuesSchema().deserialize, data
        )


class PatchJournalIssuesTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("journals", pattern="/journals/{journal_id}")
        self.config.add_route("journals", pattern="/journals/{journals_id}/issues")

        # register a journal
        self.request.matchdict = {"journal_id": "1678-4596-cr"}
        self.request.validated = apptesting.journal_registry_fixture()
        restfulapi.put_journal(self.request)

    def test_patch_journal_issues_calls_add_issue_to_journal(self):
        self.request.matchdict["journal_id"] = "1678-4596-cr"
        self.request.validated = {"issue": "1678-4596-cr-25-3"}
        MockAddIssueToJournal = Mock()
        self.request.services["add_issue_to_journal"] = MockAddIssueToJournal
        restfulapi.patch_journal_issues(self.request)
        MockAddIssueToJournal.assert_called_once_with(
            id="1678-4596-cr", issue="1678-4596-cr-25-3"
        )

    def test_patch_journal_issues_calls_insert_issue_to_journal_if_index_informed(self):
        self.request.matchdict["journal_id"] = "1678-4596-cr"
        self.request.validated = {"issue": "1678-4596-cr-25-3", "index": 0}
        MockAddIssueToJournal = Mock()
        MockInsertIssueToJournal = Mock()
        self.request.services["add_issue_to_journal"] = MockAddIssueToJournal
        self.request.services["insert_issue_to_journal"] = MockInsertIssueToJournal
        restfulapi.patch_journal_issues(self.request)
        MockInsertIssueToJournal.assert_called_once_with(
            id="1678-4596-cr", index=0, issue="1678-4596-cr-25-3"
        )
        MockAddIssueToJournal.assert_not_called()

    def test_patch_journal_issues_returns_404_if_no_journal_found(self):
        self.request.matchdict["journal_id"] = "1678-4596-cr"
        commands_data = (
            ("add_issue_to_journal", {"issue": "1678-4596-cr-25-3"}),
            ("insert_issue_to_journal", {"issue": "1678-4596-cr-25-3", "index": 2}),
        )
        for command, data in commands_data:
            with self.subTest(command=command, data=data):
                self.request.validated = data
                MockPatchJournal = Mock(
                    side_effect=exceptions.DoesNotExist("Does Not Exist")
                )
                self.request.services[command] = MockPatchJournal
                response = restfulapi.patch_journal_issues(self.request)
                self.assertIsInstance(response, HTTPNotFound)

    def test_patch_journal_issues_returns_204_if_issue_already_exists(self):
        self.request.matchdict["journal_id"] = "1678-4596-cr"
        commands_data = (
            ("add_issue_to_journal", {"issue": "1678-4596-cr-25-3"}),
            ("insert_issue_to_journal", {"issue": "1678-4596-cr-25-3", "index": 2}),
        )
        for command, data in commands_data:
            with self.subTest(command=command, data=data):
                self.request.validated = data
                MockPatchJournal = Mock(
                    side_effect=exceptions.AlreadyExists("Already Exists")
                )
                self.request.services[command] = MockPatchJournal
                response = restfulapi.patch_journal_issues(self.request)
                self.assertIsInstance(response, HTTPNoContent)

    def test_patch_journal_issues_returns_204_if_ok(self):
        self.request.matchdict["journal_id"] = "1678-4596-cr"
        commands_data = (
            ("add_issue_to_journal", {"issue": "1678-4596-cr-25-3"}),
            ("insert_issue_to_journal", {"issue": "1678-4596-cr-25-3", "index": 2}),
        )
        for command, data in commands_data:
            with self.subTest(command=command, data=data):
                self.request.validated = data
                MockPatchJournal = Mock()
                self.request.services[command] = MockPatchJournal
                response = restfulapi.patch_journal_issues(self.request)
                self.assertIsInstance(response, HTTPNoContent)


class PutJournalIssuesTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("journals", pattern="/journals/{journal_id}")
        self.config.add_route("journals", pattern="/journals/{journals_id}/issues")

        # register a journal
        self.request.matchdict = {"journal_id": "example-journal-id"}
        self.request.validated = apptesting.journal_registry_fixture()
        restfulapi.put_journal(self.request)

    def test_should_call_update_issues_in_journal(self):
        self.request.matchdict["journal_id"] = "example-journal-id"
        self.request.validated = [
            {"id": "issue-1", "year": "2019"},
            {"id": "issue-2", "year": "2019"},
        ]
        MockUpdateIssuesInJournal = Mock()
        self.request.services["update_issues_in_journal"] = MockUpdateIssuesInJournal
        restfulapi.put_journal_issues(self.request)
        MockUpdateIssuesInJournal.assert_called_once_with(
            id="example-journal-id",
            issues=[
                {"id": "issue-1", "year": "2019"},
                {"id": "issue-2", "year": "2019"},
            ],
        )

    def test_should_return_422_if_already_exists_exception_is_raised(self):
        self.request.matchdict["journal_id"] = "example-journal-id"
        self.request.validated = [
            {"id": "issue-1", "year": "2019"},
            {"id": "issue-1", "year": "2019"},
        ]
        response = restfulapi.put_journal_issues(self.request)
        self.assertIsInstance(response, HTTPUnprocessableEntity)

    def test_should_not_update_if_already_exists_exception_is_raised(self):
        self.request.matchdict["journal_id"] = "example-journal-id"
        self.request.validated = [
            {"id": "issue-1", "year": "2019"},
            {"id": "issue-1", "year": "2019"},
        ]
        restfulapi.put_journal_issues(self.request)
        response = restfulapi.get_journal(self.request)
        self.assertEqual([], response.get("items"))

    def test_should_return_404_if_journal_not_found(self):
        self.request.matchdict["journal_id"] = "example-journal-id"
        self.request.validated = [{"id": "issue-1", "year": "2019"}]
        MockUpdateIssuesInJournal = Mock(
            side_effect=exceptions.DoesNotExist("Does Not Exist")
        )
        self.request.services["update_issues_in_journal"] = MockUpdateIssuesInJournal
        response = restfulapi.put_journal_issues(self.request)
        self.assertIsInstance(response, HTTPNotFound)

    def test_should_return_204_if_journal_issues_was_updated(self):
        self.request.matchdict["journal_id"] = "example-journal-id"
        self.request.validated = [{"id": "issue-1", "year": "2019"}]
        response = restfulapi.put_journal_issues(self.request)
        self.assertIsInstance(response, HTTPNoContent)


class DeleteJournalIssuesSchemaTest(unittest.TestCase):
    def test_issue_field_is_required(self):
        self.assertRaises(
            colander.Invalid, restfulapi.DeleteJournalIssuesSchema().deserialize, {}
        )

    def test_check_fields_type(self):
        self.assertRaises(
            colander.Invalid,
            restfulapi.DeleteJournalIssuesSchema().deserialize,
            {"issue": 1678},
        )

    def test_valid(self):
        data = {"issue": "1678-4596-cr-25-3"}
        restfulapi.DeleteJournalIssuesSchema().deserialize(data)


class DeleteJournalIssuesTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("journals", pattern="/journals/{journal_id}")
        self.config.add_route("journals", pattern="/journals/{journals_id}/issues")

        # register a journal
        self.request.matchdict = {"journal_id": "1678-4596-cr"}
        self.request.validated = apptesting.journal_registry_fixture()
        restfulapi.put_journal(self.request)

    def test_delete_journal_issues_calls_remove_issue_from_journal(self):
        self.request.matchdict["journal_id"] = "1678-4596-cr"
        self.request.validated = {"issue": "1678-4596-cr-25-3"}
        MockRemoveIssueFromJournal = Mock()
        self.request.services["remove_issue_from_journal"] = MockRemoveIssueFromJournal
        restfulapi.delete_journal_issues(self.request)
        MockRemoveIssueFromJournal.assert_called_once_with(
            id="1678-4596-cr", issue="1678-4596-cr-25-3"
        )

    def test_delete_journal_issues_returns_404_if_no_journal_nor_issue_found(self):
        self.request.matchdict["journal_id"] = "1678-4596-cr"
        self.request.validated = {"issue": "1678-4596-cr-25-3"}
        MockRemoveIssueFromJournal = Mock(
            side_effect=exceptions.DoesNotExist("Does Not Exist")
        )
        self.request.services["remove_issue_from_journal"] = MockRemoveIssueFromJournal
        response = restfulapi.delete_journal_issues(self.request)
        self.assertIsInstance(response, HTTPNotFound)

    def test_delete_journal_issues_returns_204_if_ok(self):
        self.request.matchdict["journal_id"] = "1678-4596-cr"
        self.request.validated = {"issue": "1678-4596-cr-25-3"}
        MockRemoveIssueFromJournal = Mock()
        self.request.services["remove_issue_from_journal"] = MockRemoveIssueFromJournal
        response = restfulapi.delete_journal_issues(self.request)
        self.assertIsInstance(response, HTTPNoContent)


class JournalAOPSchemaTest(unittest.TestCase):
    def test_aop_is_required(self):
        self.assertRaises(
            colander.Invalid, restfulapi.JournalAOPSchema().deserialize, {}
        )

    def test_should_be_valid(self):
        restfulapi.JournalAOPSchema().deserialize({"aop": "001"})


class PatchAOPJournalUnitTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("journals", pattern="/journals/{journal_id}/aop")

        # register a journal
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = apptesting.journal_registry_fixture()
        restfulapi.put_journal(self.request)

    def test_should_raise_exception_if_journal_does_not_exists(self):
        self.request.matchdict = {"journal_id": "random-journal-id"}
        self.request.validated = {"aop": "001"}
        self.request.services["set_ahead_of_print_bundle_to_journal"] = Mock(
            side_effect=exceptions.DoesNotExist()
        )
        self.assertIsInstance(restfulapi.patch_journal_aop(self.request), HTTPNotFound)

    def test_should_add_aop_to_journal(self):
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = {"aop": "001"}
        self.assertIsInstance(restfulapi.patch_journal_aop(self.request), HTTPNoContent)


class DeleteJournalAopTest(unittest.TestCase):
    def setUp(self):
        self.request = make_request()
        self.config = testing.setUp()
        self.config.add_route("journals", pattern="/journals/{journal_id}/aop")

        # register a journal
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.validated = apptesting.journal_registry_fixture()
        restfulapi.put_journal(self.request)

    def test_should_raise_exception_if_journal_does_not_exists(self):
        self.request.matchdict = {"journal_id": "random-journal-id"}
        self.request.services["set_ahead_of_print_bundle_to_journal"] = Mock(
            side_effect=exceptions.DoesNotExist()
        )
        self.assertIsInstance(restfulapi.delete_journal_aop(self.request), HTTPNotFound)

    def test_should_raise_exception_if_journal_does_not_has_aop(self):
        self.request.matchdict = {"journal_id": "1678-4596-cr-49-02"}
        self.request.services["set_ahead_of_print_bundle_to_journal"] = Mock(
            side_effect=exceptions.DoesNotExist()
        )
        self.assertIsInstance(restfulapi.delete_journal_aop(self.request), HTTPNotFound)

    def test_should_remove_aop_from_journal(self):
        # add aop to journal
        self.request.validated = {"aop": "001"}
        restfulapi.patch_journal_aop(self.request)

        self.assertIsInstance(
            restfulapi.delete_journal_aop(self.request), HTTPNoContent
        )


@patch("documentstore.domain.fetch_data", new=fetch_data_stub)
class FetchDocumentRenditionsUnitTests(unittest.TestCase):
    def test_when_doesnt_exist_returns_http_404(self):
        request = make_request()
        request.matchdict = {"document_id": "unknown"}
        self.assertRaises(HTTPNotFound, restfulapi.fetch_document_renditions, request)

    def test_latest_version_returns_list_of_dicts(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.services["register_document"](
            id="my-testing-doc",
            data_url="https://raw.githubusercontent.com/scieloorg/packtools/master/tests/samples/0034-8910-rsp-48-2-0347.xml",
            assets={},
        )

        renditions = restfulapi.fetch_document_renditions(request)
        self.assertIsInstance(renditions, list)
        expected_fields = set(["filename", "lang", "mimetype", "data", "size_bytes"])
        for rendition in renditions:
            for field in rendition.keys():
                self.assertTrue(field in expected_fields)

    def test_versions_prior_to_creation_returns_http_404(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.GET = {"when": "1900-01-01"}
        request.services["register_document"](
            id="my-testing-doc",
            data_url="https://raw.githubusercontent.com/scieloorg/packtools/master/tests/samples/0034-8910-rsp-48-2-0347.xml",
            assets={},
        )
        self.assertRaises(HTTPNotFound, restfulapi.fetch_document_renditions, request)

    def test_versions_in_distant_future_returns_list(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.GET = {"when": "2100-01-01"}
        request.services["register_document"](
            id="my-testing-doc",
            data_url="https://raw.githubusercontent.com/scieloorg/packtools/master/tests/samples/0034-8910-rsp-48-2-0347.xml",
            assets={},
        )

        document_data = restfulapi.fetch_document_renditions(request)
        self.assertIsInstance(document_data, list)


class RegisterDocumentVersionUnitTests(unittest.TestCase):
    def test_input_arguments(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.validated = {
            "filename": "0034-8910-rsp-48-2-0347.xml",
            "data_url": "https://files.scielo.br/aksjhdf/0034-8910-rsp-48-2-0347.pdf",
            "mimetype": "application/pdf",
            "lang": "pt",
            "size_bytes": 23456,
        }
        request.services["register_rendition_version"] = Mock()
        restfulapi.register_rendition_version(request)
        request.services["register_rendition_version"].assert_called_once_with(
            "my-testing-doc",
            "0034-8910-rsp-48-2-0347.xml",
            "https://files.scielo.br/aksjhdf/0034-8910-rsp-48-2-0347.pdf",
            "application/pdf",
            "pt",
            23456,
        )

    def test_returns_HTTPNoContent_on_success(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.validated = {
            "filename": "0034-8910-rsp-48-2-0347.xml",
            "data_url": "https://files.scielo.br/aksjhdf/0034-8910-rsp-48-2-0347.pdf",
            "mimetype": "application/pdf",
            "lang": "pt",
            "size_bytes": 23456,
        }
        request.services["register_rendition_version"] = Mock()
        response = restfulapi.register_rendition_version(request)
        self.assertIsInstance(response, HTTPNoContent)

    def test_returns_HTTPNotFound_when_document_doesnt_exist(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.validated = {
            "filename": "0034-8910-rsp-48-2-0347.xml",
            "data_url": "https://files.scielo.br/aksjhdf/0034-8910-rsp-48-2-0347.pdf",
            "mimetype": "application/pdf",
            "lang": "pt",
            "size_bytes": 23456,
        }
        request.services["register_rendition_version"] = Mock(
            side_effect=exceptions.DoesNotExist()
        )
        response = restfulapi.register_rendition_version(request)
        self.assertIsInstance(response, HTTPNotFound)

    def test_returns_HTTPNoContent_when_already_exists(self):
        request = make_request()
        request.matchdict = {"document_id": "my-testing-doc"}
        request.validated = {
            "filename": "0034-8910-rsp-48-2-0347.xml",
            "data_url": "https://files.scielo.br/aksjhdf/0034-8910-rsp-48-2-0347.pdf",
            "mimetype": "application/pdf",
            "lang": "pt",
            "size_bytes": 23456,
        }
        request.services["register_rendition_version"] = Mock(
            side_effect=exceptions.VersionAlreadySet()
        )
        response = restfulapi.register_rendition_version(request)
        self.assertIsInstance(response, HTTPNoContent)


class DeleteDocumentUnitTests(unittest.TestCase):
    def test_when_doesnt_exist_returns_http_404(self):
        request = make_request()
        request.matchdict = {"document_id": "unknown"}
        request.services["delete_document"] = Mock(side_effect=exceptions.DoesNotExist)
        self.assertRaises(HTTPNotFound, restfulapi.delete_document, request)

    def test_when_already_deleted_returns_HTTPNoContent(self):
        request = make_request()
        request.matchdict = {"document_id": "unknown"}
        request.services["delete_document"] = Mock(
            side_effect=exceptions.VersionAlreadySet
        )
        self.assertRaises(HTTPNoContent, restfulapi.delete_document, request)

    def test_returns_HTTPNoContent_on_success(self):
        request = make_request()
        request.matchdict = {"document_id": "unknown"}
        request.services["delete_document"] = Mock()
        self.assertRaises(HTTPNoContent, restfulapi.delete_document, request)
