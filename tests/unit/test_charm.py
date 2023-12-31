# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import unittest
from unittest.mock import Mock, call, patch

from ops import testing
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ChangeError, ExecError

from charm import UERANSIMOperatorCharm

MULTUS_LIB_PATH = "charms.kubernetes_charm_libraries.v0.multus"
GNB_IDENTITY_LIB_PATH = "charms.sdcore_gnbsim.v0.fiveg_gnb_identity"


def read_file(path: str) -> str:
    """Reads a file and returns as a string.

    Args:
        path (str): path to the file.

    Returns:
        str: content of the file.
    """
    with open(path, "r") as f:
        content = f.read()
    return content


class TestCharm(unittest.TestCase):
    @patch("lightkube.core.client.GenericSyncClient")
    @patch(
        "charm.KubernetesServicePatch",
        lambda charm, ports: None,
    )
    def setUp(self, patch_k8s_client):
        self.namespace = "whatever"
        self.harness = testing.Harness(UERANSIMOperatorCharm)
        self.harness.set_model_name(name=self.namespace)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def _create_n2_relation(self) -> int:
        """Creates a relation between gnbsim and amf.

        Returns:
            int: Id of the created relation
        """
        amf_relation_id = self.harness.add_relation(relation_name="fiveg-n2", remote_app="amf")
        self.harness.add_relation_unit(relation_id=amf_relation_id, remote_unit_name="amf/0")
        return amf_relation_id

    def _n2_data_available(self) -> None:
        """Creates the N2 relation and sets the relation data in the n2 relation."""
        amf_relation_id = self._create_n2_relation()
        self.harness.update_relation_data(
            relation_id=amf_relation_id,
            app_or_unit="amf",
            key_values={
                "amf_hostname": "amf",
                "amf_port": "38412",
                "amf_ip_address": "1.1.1.1",
            },
        )

    def test_given_default_config_when_config_changed_then_status_is_blocked(
        self,
    ):
        self.harness.update_config(key_values={"tac": ""})

        self.assertEqual(
            self.harness.charm.unit.status,
            BlockedStatus("Configurations are invalid: ['tac']"),
        )

    def test_given_cant_connect_to_workload_when_config_changed_then_status_is_waiting(
        self,
    ):
        self._create_n2_relation()
        self.harness.set_can_connect(container="ueransim", val=False)

        self.harness.update_config(key_values={})

        self.assertEqual(
            self.harness.charm.unit.status,
            WaitingStatus("Waiting for container to be ready"),
        )

    @patch("ops.model.Container.exists")
    def test_given_storage_not_attached_when_config_changed_then_status_is_waiting(
        self,
        patch_exists,
    ):
        patch_exists.return_value = False
        self.harness.set_can_connect(container="ueransim", val=True)
        self._create_n2_relation()

        self.harness.update_config(key_values={})

        self.assertEqual(
            self.harness.charm.unit.status,
            WaitingStatus("Waiting for storage to be attached"),
        )

    @patch(f"{MULTUS_LIB_PATH}.KubernetesMultusCharmLib.is_ready")
    @patch("ops.model.Container.exists")
    def test_given_multus_not_ready_when_config_changed_then_status_is_waiting(
        self,
        patch_exists,
        patch_is_ready,
    ):
        patch_exists.return_value = True
        patch_is_ready.return_value = False
        self.harness.set_can_connect(container="ueransim", val=True)
        self._create_n2_relation()

        self.harness.update_config(key_values={})

        self.assertEqual(
            self.harness.charm.unit.status,
            WaitingStatus("Waiting for Multus to be ready"),
        )

    def test_given_n2_relation_not_created_when_config_changed_then_status_is_blocked(
        self,
    ):
        self.harness.update_config(key_values={})

        self.assertEqual(
            self.harness.charm.unit.status,
            BlockedStatus("Waiting for N2 relation to be created"),
        )

    @patch("ops.model.Container.push")
    @patch(f"{MULTUS_LIB_PATH}.KubernetesMultusCharmLib.is_ready")
    @patch("ops.model.Container.exec", new=Mock)
    @patch("ops.model.Container.exists")
    def test_given_n2_information_not_available_when_config_changed_then_status_is_waiting(
        self,
        patch_dir_exists,
        patch_is_ready,
        patch_push,
    ):
        patch_is_ready.return_value = True
        patch_dir_exists.return_value = True
        self.harness.set_can_connect(container="ueransim", val=True)
        self._create_n2_relation()

        self.harness.update_config(key_values={})

        self.assertEqual(
            self.harness.charm.unit.status,
            WaitingStatus("Waiting for N2 information"),
        )

    @patch("ops.model.Container.push")
    @patch(f"{MULTUS_LIB_PATH}.KubernetesMultusCharmLib.is_ready")
    @patch("ops.model.Container.exec", new=Mock)
    @patch("ops.model.Container.exists")
    def test_given_default_config_and_n2_info_when_config_changed_then_config_is_written_to_workload(  # noqa: E501
        self,
        patch_dir_exists,
        patch_is_ready,
        patch_push,
    ):
        patch_is_ready.return_value = True
        patch_dir_exists.return_value = True
        self.harness.set_can_connect(container="ueransim", val=True)

        self._n2_data_available()

        self.harness.update_config(key_values={})

        expected_config_file_content = read_file("tests/unit/expected_gnb_config.yaml")
        patch_push.assert_called_with(
            source=expected_config_file_content, path="/etc/ueransim/gnb.yaml"
        )

    @patch("ops.model.Container.push")
    @patch(f"{MULTUS_LIB_PATH}.KubernetesMultusCharmLib.is_ready")
    @patch("ops.model.Container.exec", new=Mock)
    @patch("ops.model.Container.exists")
    def test_given_default_config_and_n2_info_available_when_n2_relation_joined_then_config_is_written_to_workload(  # noqa: E501
        self,
        patch_dir_exists,
        patch_is_ready,
        patch_push,
    ):
        patch_is_ready.return_value = True
        patch_dir_exists.return_value = True
        self.harness.set_can_connect(container="ueransim", val=True)

        self._n2_data_available()

        expected_config_file_content = read_file("tests/unit/expected_gnb_config.yaml")
        patch_push.assert_called_with(
            source=expected_config_file_content, path="/etc/ueransim/gnb.yaml"
        )
        
    @patch("ops.model.Container.push", new=Mock)
    @patch(f"{MULTUS_LIB_PATH}.KubernetesMultusCharmLib.is_ready")
    @patch("ops.model.Container.exec", new=Mock)
    @patch("ops.model.Container.exists")
    def test_given_default_config_when_config_changed_then_status_is_active(
        self,
        patch_dir_exists,
        patch_is_ready,
    ):
        patch_is_ready.return_value = True
        patch_dir_exists.return_value = True
        self.harness.set_can_connect(container="ueransim", val=True)

        self._n2_data_available()

        self.harness.update_config(key_values={})

        self.assertEqual(self.harness.charm.unit.status, ActiveStatus())
