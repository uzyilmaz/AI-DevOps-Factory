from pathlib import Path
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from agents.lead_agent.agent import execute_project_generation
from models.project_spec import ProjectSpec
from services.devops_assessment import (
    compute_devops_score,
    get_maturity_level,
)
from services.devops_gap_service import get_devops_gap_report
from services.project_spec_builder import build_project_spec_from_repository
from services.report_generator import generate_devops_report
from services.roadmap_generator import generate_devops_roadmap


CiCdPlatform = Literal[
    "azure_devops",
    "github_actions",
    "jenkins",
    "gitlab_ci",
]
DeliveryMethod = Literal["source_code", "container_image"]


class AnalysisRequest(BaseModel):
    project_path: str
    target_ci_cd_platform: CiCdPlatform = "azure_devops"
    delivery_method: DeliveryMethod = "source_code"


class GapReport(BaseModel):
    selected_ci_cd_platform: CiCdPlatform
    selected_delivery_method: DeliveryMethod
    assets: dict[str, bool]
    gaps: dict[str, bool]


class GenerationRequest(BaseModel):
    project_path: str
    specification: ProjectSpec
    gap_report: GapReport


class ProjectAssessment(BaseModel):
    specification: ProjectSpec
    gap_report: GapReport
    score: int
    maturity: str
    assessment_report: str
    roadmap: list[dict[str, int | str]]


class GenerationResponse(BaseModel):
    generated_project_path: str
    assessment: ProjectAssessment


app = FastAPI(title="AI DevOps Factory API")


def validate_project_path(project_path: str) -> str:
    path = Path(project_path).expanduser()
    if not path.exists():
        raise HTTPException(status_code=400, detail="Le chemin indiqué n'existe pas.")
    if not path.is_dir():
        raise HTTPException(
            status_code=400,
            detail="Le chemin doit correspondre à un dossier.",
        )
    resolved_path = path.resolve()
    projects_root = os.getenv("PROJECTS_ROOT")
    if projects_root and not resolved_path.is_relative_to(
        Path(projects_root).resolve()
    ):
        raise HTTPException(
            status_code=400,
            detail="Le projet doit se trouver dans le dossier partagé /workspace.",
        )
    return str(resolved_path)


def build_assessment(
    project_path: str,
    specification: ProjectSpec,
    target_ci_cd_platform: CiCdPlatform,
    delivery_method: DeliveryMethod,
) -> ProjectAssessment:
    gap_report = get_devops_gap_report(
        project_path,
        target_ci_cd_platform=target_ci_cd_platform,
        delivery_method=delivery_method,
    )
    score = compute_devops_score(
        gap_report["assets"],
        delivery_method,
        target_ci_cd_platform,
    )
    return ProjectAssessment(
        specification=specification,
        gap_report=gap_report,
        score=score,
        maturity=get_maturity_level(score),
        assessment_report=generate_devops_report(specification, gap_report),
        roadmap=generate_devops_roadmap(gap_report),
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/analyze", response_model=ProjectAssessment)
def analyze_project(request: AnalysisRequest) -> ProjectAssessment:
    project_path = validate_project_path(request.project_path)
    specification = build_project_spec_from_repository(project_path)
    return build_assessment(
        project_path,
        specification,
        request.target_ci_cd_platform,
        request.delivery_method,
    )


@app.post("/generate", response_model=GenerationResponse)
def generate_assets(request: GenerationRequest) -> GenerationResponse:
    project_path = validate_project_path(request.project_path)
    gap_report = request.gap_report.model_dump()
    generated_project_path = execute_project_generation(
        request.specification,
        gap_report,
        project_path,
    )
    assessment = build_assessment(
        project_path,
        request.specification,
        request.gap_report.selected_ci_cd_platform,
        request.gap_report.selected_delivery_method,
    )
    return GenerationResponse(
        generated_project_path=generated_project_path,
        assessment=assessment,
    )
