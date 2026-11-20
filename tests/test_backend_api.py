import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from models.project_spec import ProjectSpec


class BackendApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.specification = ProjectSpec(
            project_name="python-project",
            language="python",
            framework="unknown",
            database="unknown",
            build_tool="pip",
            cloud="unknown",
            environments=["dev"],
        )
        self.gap_report = {
            "selected_ci_cd_platform": "azure_devops",
            "selected_delivery_method": "source_code",
            "assets": {
                "readme": True,
                "gitignore": True,
                "azure_pipeline": True,
                "dockerfile": False,
                "docker_compose": False,
            },
            "gaps": {
                "generate_pipeline": False,
                "generate_dockerfile": False,
                "generate_docker_compose": False,
                "generate_readme": False,
                "generate_gitignore": False,
            },
        }

    def test_analyze_returns_project_assessment(self):
        with tempfile.TemporaryDirectory() as project_path:
            with patch.dict(os.environ, {"PROJECTS_ROOT": project_path}):
                with patch(
                    "backend.main.build_project_spec_from_repository",
                    return_value=self.specification,
                ), patch(
                    "backend.main.get_devops_gap_report",
                    return_value=self.gap_report,
                ):
                    response = self.client.post(
                        "/analyze",
                        json={"project_path": project_path},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["specification"]["project_name"],
            "python-project",
        )
        self.assertEqual(response.json()["score"], 100)

    def test_generate_returns_refreshed_assessment(self):
        with tempfile.TemporaryDirectory() as project_path:
            with patch.dict(os.environ, {"PROJECTS_ROOT": project_path}):
                with patch(
                    "backend.main.execute_project_generation",
                    return_value=project_path,
                ), patch(
                    "backend.main.get_devops_gap_report",
                    return_value=self.gap_report,
                ):
                    response = self.client.post(
                        "/generate",
                        json={
                            "project_path": project_path,
                            "specification": self.specification.model_dump(),
                            "gap_report": self.gap_report,
                        },
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["generated_project_path"],
            project_path,
        )
        self.assertEqual(response.json()["assessment"]["score"], 100)


if __name__ == "__main__":
    unittest.main()
