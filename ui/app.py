from io import BytesIO
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

import streamlit as st


# Permet à Streamlit de trouver les modules du projet
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from agents.lead_agent.agent import (
    execute_project_generation
)

from services.project_spec_builder import (
    build_project_spec_from_repository
)

from services.devops_gap_service import (
    get_devops_gap_report
)

from services.devops_assessment import (
    compute_devops_score,
    get_maturity_level
)

from services.report_generator import (
    generate_devops_report
)

from services.roadmap_generator import (
    generate_devops_roadmap
)

from services.devops_options import (
    CI_CD_PLATFORM_OPTIONS,
    CI_CD_PLATFORM_DISPLAY_NAMES,
    CI_CD_PLATFORM_ASSET_KEYS,
    DELIVERY_METHOD_OPTIONS,
    DELIVERY_METHOD_DISPLAY_NAMES
)


# ---------------------------------------------------------
# Configuration Streamlit
# ---------------------------------------------------------

st.set_page_config(
    page_title="AI DevOps Factory",
    page_icon="⚙️",
    layout="wide"
)


# ---------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------

def initialize_session_state():

    default_values = {
        "analysis_completed": False,
        "specification": None,
        "gap_report": None,
        "assessment_report": None,
        "roadmap": None,
        "score": None,
        "maturity": None,
        "analyzed_project_path": None,
        "analyzed_project_source": None,
        "previous_project_path": None,
        "previous_project_source": None,
        "uploaded_project_name": None,
        "generated_assets_zip": None,
        "generated_assets_zip_name": None
    }

    for key, value in default_values.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_analysis():

    cleanup_uploaded_project(
        st.session_state.get("analyzed_project_path")
        if st.session_state.get("analyzed_project_source") == "upload"
        else None
    )

    st.session_state.analysis_completed = False
    st.session_state.specification = None
    st.session_state.gap_report = None
    st.session_state.assessment_report = None
    st.session_state.roadmap = None
    st.session_state.score = None
    st.session_state.maturity = None
    st.session_state.analyzed_project_path = None
    st.session_state.analyzed_project_source = None
    st.session_state.previous_project_path = None
    st.session_state.previous_project_source = None
    st.session_state.uploaded_project_name = None
    st.session_state.generated_assets_zip = None
    st.session_state.generated_assets_zip_name = None
    st.session_state.pop("project_folder_upload", None)


def cleanup_uploaded_project(project_path):

    if not project_path:
        return

    path = Path(project_path).resolve()
    temporary_directory = Path(tempfile.gettempdir()).resolve()

    if (
        path.parent == temporary_directory
        and path.name.startswith("ai-devops-upload-")
        and path.is_dir()
    ):
        shutil.rmtree(path)


def validate_project_path(project_path: str):

    if not project_path.strip():
        return False, "Veuillez saisir le chemin d'un projet."

    path = Path(project_path).expanduser()

    if not path.exists():
        return False, "Le chemin indiqué n'existe pas."

    if not path.is_dir():
        return False, "Le chemin doit correspondre à un dossier."

    return True, ""


def create_uploaded_project(uploaded_files):

    if not uploaded_files:
        raise ValueError("Veuillez sélectionner un dossier à importer.")

    relative_paths = []

    for uploaded_file in uploaded_files:
        raw_path = uploaded_file.name.replace("\\", "/")
        relative_path = PurePosixPath(raw_path)

        if (
            relative_path.is_absolute()
            or any(part in {"", ".", ".."} for part in raw_path.split("/"))
            or (relative_path.parts and relative_path.parts[0].endswith(":"))
        ):
            raise ValueError(
                f"Chemin de fichier invalide dans le dossier importé: "
                f"{uploaded_file.name}"
            )

        relative_paths.append(relative_path)

    common_root = relative_paths[0].parts[0] if relative_paths else ""
    strip_common_root = (
        all(len(path.parts) > 1 for path in relative_paths)
        and all(path.parts[0] == common_root for path in relative_paths)
    )
    project_name = common_root if strip_common_root else "uploaded-project"
    workspace = Path(tempfile.mkdtemp(prefix="ai-devops-upload-"))

    try:
        for uploaded_file, relative_path in zip(
            uploaded_files,
            relative_paths
        ):
            project_path = (
                PurePosixPath(*relative_path.parts[1:])
                if strip_common_root
                else relative_path
            )
            target_path = workspace.joinpath(*project_path.parts)
            resolved_target = target_path.resolve()

            if not resolved_target.is_relative_to(workspace.resolve()):
                raise ValueError(
                    f"Chemin de fichier invalide dans le dossier importé: "
                    f"{uploaded_file.name}"
                )

            if target_path.exists():
                raise ValueError(
                    f"Le dossier importé contient un chemin en double: "
                    f"{uploaded_file.name}"
                )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(uploaded_file.getvalue())

    except Exception:
        cleanup_uploaded_project(workspace)
        raise

    return workspace, project_name


def list_project_files(project_path):

    root = Path(project_path).resolve()

    return {
        path.relative_to(root)
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def create_generated_assets_archive(project_path, previous_files):

    root = Path(project_path).resolve()
    generated_files = sorted(
        path
        for path in list_project_files(root) - previous_files
        if (root / path).resolve().is_relative_to(root)
    )

    if not generated_files:
        raise RuntimeError(
            "Aucun nouveau fichier DevOps n'a été créé."
        )

    archive = BytesIO()

    with ZipFile(archive, "w", ZIP_DEFLATED) as zip_file:
        for relative_path in generated_files:
            zip_file.write(
                root / relative_path,
                arcname=relative_path.as_posix()
            )

    return archive.getvalue()


def display_asset_status(
    label: str,
    present: bool
):

    if present:
        st.success(f"{label}: Present")
    else:
        st.warning(f"{label}: Missing")


def get_selected_value(
    display_value: str,
    options: dict
):

    return options[display_value]


# ---------------------------------------------------------
# Initialisation
# ---------------------------------------------------------

initialize_session_state()


# ---------------------------------------------------------
# En-tête
# ---------------------------------------------------------

st.title("⚙️ AI DevOps Factory")

st.write(
    """
Analysez un projet existant, identifiez les éléments DevOps
présents ou manquants, obtenez un score de maturité et générez
uniquement les composants nécessaires.
"""
)


# ---------------------------------------------------------
# Barre latérale
# ---------------------------------------------------------

with st.sidebar:

    st.header("Configuration")

    selected_platform_label = st.selectbox(
        "CI/CD Platform",
        options=list(
            CI_CD_PLATFORM_OPTIONS.keys()
        ),
        index=0
    )

    selected_delivery_label = st.selectbox(
        "Delivery Method",
        options=list(
            DELIVERY_METHOD_OPTIONS.keys()
        ),
        index=0
    )

    selected_platform = get_selected_value(
        selected_platform_label,
        CI_CD_PLATFORM_OPTIONS
    )

    selected_delivery_method = get_selected_value(
        selected_delivery_label,
        DELIVERY_METHOD_OPTIONS
    )

    st.divider()

    st.subheader("Selected configuration")

    st.write(
        f"CI/CD: {selected_platform_label}"
    )

    st.write(
        f"Delivery: {selected_delivery_label}"
    )

    st.divider()

    if st.button(
        "Reset analysis",
        width="stretch"
    ):
        reset_analysis()
        st.rerun()


# ---------------------------------------------------------
# Sélection du projet
# ---------------------------------------------------------

st.subheader("1. Select an existing project")

project_source = st.radio(
    "Project location",
    ["Upload a folder from this computer", "Use a server path"],
    horizontal=True
)

uploaded_files = None

if project_source == "Upload a folder from this computer":
    uploaded_files = st.file_uploader(
        "Project folder",
        accept_multiple_files="directory",
        key="project_folder_upload",
        help=(
            "The selected files will be uploaded to the server for analysis. "
            "The server cannot access files that remain only on your computer."
        )
    )
    st.caption(
        "The folder is temporarily copied to the server. "
        "Generated assets can be downloaded as a ZIP."
    )
else:
    project_path = st.text_input(
        "Project directory on the server",
        placeholder=(
            r"C:\Users\YourName\Projects\MyProject"
        ),
        help=(
            "Use this option only when the project directory is already "
            "accessible on the server."
        )
    )


analyze_button = st.button(
    "🔍 Analyze project",
    type="primary",
    width="stretch"
)


# ---------------------------------------------------------
# Analyse du projet
# ---------------------------------------------------------

if analyze_button:

    uploaded_project_path = None

    if project_source == "Upload a folder from this computer":
        try:
            uploaded_project_path, uploaded_project_name = (
                create_uploaded_project(uploaded_files)
            )
            normalized_project_path = str(uploaded_project_path)
            source_kind = "upload"

        except Exception as error:
            st.error(str(error))
            normalized_project_path = None

    else:
        is_valid, error_message = validate_project_path(
            project_path
        )

        if not is_valid:
            st.error(error_message)
            normalized_project_path = None
        else:
            normalized_project_path = str(
                Path(project_path).resolve()
            )
            uploaded_project_name = None
            source_kind = "server"

    if normalized_project_path:
        try:

            with st.spinner(
                "Repository analysis in progress..."
            ):

                specification = (
                    build_project_spec_from_repository(
                        normalized_project_path
                    )
                )

                
                gap_report = get_devops_gap_report(
                    normalized_project_path,
                    target_ci_cd_platform=(
                        selected_platform
                    ),
                    delivery_method=(
                        selected_delivery_method
                    )
                )
    
                score = compute_devops_score(
                    gap_report["assets"],
                    selected_delivery_method,
                    selected_platform
                )

                maturity = get_maturity_level(
                    score
                )

                assessment_report = (
                    generate_devops_report(
                        specification,
                        gap_report
                    )
                )

                roadmap = generate_devops_roadmap(
                    gap_report
                )

            st.session_state.specification = (
                specification
            )

            st.session_state.gap_report = (
                gap_report
            )

            st.session_state.score = score

            st.session_state.maturity = maturity

            st.session_state.assessment_report = (
                assessment_report
            )

            st.session_state.roadmap = roadmap

            st.session_state.analyzed_project_path = (
                normalized_project_path
            )

            previous_project_path = st.session_state.get(
                "previous_project_path"
            )
            previous_source = st.session_state.get(
                "previous_project_source"
            )
            if (
                previous_source == "upload"
                and previous_project_path != normalized_project_path
            ):
                cleanup_uploaded_project(previous_project_path)

            st.session_state.analyzed_project_source = source_kind
            st.session_state.uploaded_project_name = uploaded_project_name
            st.session_state.generated_assets_zip = None
            st.session_state.generated_assets_zip_name = None
            st.session_state.analysis_completed = True
            st.session_state.previous_project_path = normalized_project_path
            st.session_state.previous_project_source = source_kind

            st.success(
                "Project analysis completed successfully."
            )

        except Exception as error:

            st.session_state.analysis_completed = False
            if uploaded_project_path:
                cleanup_uploaded_project(uploaded_project_path)

            st.error(
                "The project analysis failed."
            )

            st.exception(error)


# ---------------------------------------------------------
# Affichage des résultats
# ---------------------------------------------------------

if st.session_state.analysis_completed:

    specification = (
        st.session_state.specification
    )

    gap_report = (
        st.session_state.gap_report
    )

    assets = gap_report["assets"]

    score = st.session_state.score

    maturity = st.session_state.maturity

    assessment_report = (
        st.session_state.assessment_report
    )

    roadmap = st.session_state.roadmap

    analyzed_project_path = (
        st.session_state.analyzed_project_path
    )

    platform = gap_report[
        "selected_ci_cd_platform"
    ]

    delivery_method = gap_report[
        "selected_delivery_method"
    ]

    platform_label = (
        CI_CD_PLATFORM_DISPLAY_NAMES.get(
            platform,
            platform
        )
    )

    delivery_label = (
        DELIVERY_METHOD_DISPLAY_NAMES.get(
            delivery_method,
            delivery_method
        )
    )

    detected_platforms = assets.get(
    "ci_cd_platforms",
    []
    )

    detected_platform_labels = [
        CI_CD_PLATFORM_DISPLAY_NAMES.get(
            item,
            item
        )
        for item in detected_platforms
    ]

    st.subheader(
        "Detected Platform"
    )

    if detected_platform_labels:

        st.success(
            "Detected CI/CD Platform(s): "
            + ", ".join(
                detected_platform_labels
            )
        )

    else:

        st.warning(
            "No CI/CD platform detected."
        )

    if assets.get(
        "dockerfile",
        False
    ):

        st.success(
            "Detected Delivery Method: "
            "Container Image"
        )

    else:

        st.info(
            "Detected Delivery Method: "
            "Source Code"
        )

    if detected_platforms:

        if platform in detected_platforms:

            st.success(
                f"Selected platform ({platform_label}) "
                "matches the detected platform."
            )

        else:

            st.warning(
                f"Selected platform ({platform_label}) "
                "differs from the detected platform(s): "
                f"{', '.join(detected_platform_labels)}"
            )

    st.divider()

    st.subheader("2. Project analysis")

    project_col1, project_col2 = st.columns(2)

    with project_col1:

        st.info(
            f"Project: {specification.project_name}"
        )

        st.write(
            f"Language: `{specification.language}`"
        )

        st.write(
            f"Framework: `{specification.framework}`"
        )

        st.write(
            f"Database: "
            f"`{specification.database or 'unknown'}`"
        )

    with project_col2:

        st.info(
            f"Build tool: "
            f"{specification.build_tool or 'unknown'}"
        )

        st.write(
            f"CI/CD platform: `{platform_label}`"
        )

        st.write(
            f"Delivery method: `{delivery_label}`"
        )

        if st.session_state.analyzed_project_source == "upload":
            st.write(
                f"Project source: Uploaded folder "
                f"(`{st.session_state.uploaded_project_name}`)"
            )
        else:
            st.write(
                f"Project path: `{analyzed_project_path}`"
            )

    st.divider()

    st.subheader("3. DevOps maturity")

    metric_col1, metric_col2, metric_col3 = (
        st.columns(3)
    )

    with metric_col1:

        st.metric(
            label="DevOps Score",
            value=f"{score}/100"
        )

    with metric_col2:

        st.metric(
            label="Maturity Level",
            value=maturity
        )

    with metric_col3:

        missing_asset_count = sum(
            1
            for value in gap_report["gaps"].values()
            if value
        )

        st.metric(
            label="Missing Assets",
            value=missing_asset_count
        )

    st.progress(
        score / 100,
        text=f"DevOps maturity: {score}%"
    )

    st.divider()

    st.subheader("4. Existing DevOps assets")

    assets_col1, assets_col2 = st.columns(2)

    with assets_col1:

        display_asset_status(
            "README",
            assets.get("readme", False)
        )

        display_asset_status(
            ".gitignore",
            assets.get("gitignore", False)
        )

        if delivery_method == "container_image":

            display_asset_status(
                "Dockerfile",
                assets.get(
                    "dockerfile",
                    False
                )
            )

            display_asset_status(
                "Docker Compose",
                assets.get(
                    "docker_compose",
                    False
                )
            )

        else:

            st.info(
                "Containerization assets are not required "
                "for the selected delivery method."
            )

    with assets_col2:

        platform = gap_report[
            "selected_ci_cd_platform"
        ]

        pipeline_asset_key = (
            CI_CD_PLATFORM_ASSET_KEYS.get(
                platform
            )
        )

        pipeline_label = (
            CI_CD_PLATFORM_DISPLAY_NAMES.get(
                platform,
                platform
            )
        )

        display_asset_status(
            f"{pipeline_label} Pipeline",
            assets.get(
                pipeline_asset_key,
                False
            )
        )

        display_asset_status(
            "Kubernetes",
            assets.get(
                "kubernetes",
                False
            )
        )

        display_asset_status(
            "Terraform",
            assets.get(
                "terraform",
                False
            )
        )

    st.divider()

    st.subheader("5. Recommended roadmap")

    if roadmap:

        roadmap_rows = []

        for item in roadmap:

            roadmap_rows.append({
                "Priority": item["priority"],
                "Task": item["task"],
                "Expected Impact": item["impact"]
            })

        st.dataframe(
            roadmap_rows,
            width="stretch",
            hide_index=True
        )

    else:

        st.success(
            "No basic DevOps remediation is required."
        )

    st.divider()

    st.subheader("6. Full assessment report")

    with st.expander(
        "Open the assessment report",
        expanded=False
    ):

        st.code(
            assessment_report,
            language="text"
        )

        st.download_button(
            label="Download report",
            data=assessment_report,
            file_name=(
                f"{specification.project_name}"
                "-devops-assessment.txt"
            ),
            mime="text/plain"
        )

    st.divider()

    st.subheader("7. Generate missing DevOps assets")

    if st.session_state.analyzed_project_source == "upload":
        st.warning(
            "The generated files will be added to the temporary server copy. "
            "Download the generated assets ZIP to retrieve them."
        )
    else:
        st.warning(
            "The generated files will be written directly "
            "into the analyzed project directory. "
            "Existing detected files should not be overwritten."
        )

    generation_enabled = any(
        gap_report["gaps"].values()
    )

    generate_button = st.button(
        "🛠️ Generate missing assets",
        type="primary",
        disabled=not generation_enabled,
        width="stretch"
    )

    if not generation_enabled:

        st.success(
            "All required basic DevOps assets are already present."
        )

    if generate_button:

        try:

            with st.spinner(
                "Generating missing DevOps assets..."
            ):

                previous_files = list_project_files(
                    analyzed_project_path
                )

                generated_project_path = (
                    execute_project_generation(
                        specification,
                        gap_report,
                        analyzed_project_path
                    )
                )

                generated_assets_zip = (
                    create_generated_assets_archive(
                        generated_project_path,
                        previous_files
                    )
                )

            project_archive_name = (
                st.session_state.uploaded_project_name
                if st.session_state.analyzed_project_source == "upload"
                else specification.project_name
            )
            safe_archive_name = "".join(
                character
                if character.isalnum() or character in "-_."
                else "_"
                for character in project_archive_name
            ).strip("._")
            st.session_state.generated_assets_zip = generated_assets_zip
            st.session_state.generated_assets_zip_name = (
                f"{safe_archive_name or 'project'}-generated-assets.zip"
            )

            st.success(
                "Missing DevOps assets generated "
                "successfully."
            )

            if st.session_state.analyzed_project_source != "upload":
                st.write(
                    f"Output path: "
                    f"`{generated_project_path}`"
                )

            refreshed_gap_report = (
                get_devops_gap_report(
                    analyzed_project_path,
                    target_ci_cd_platform=(
                        platform
                    ),
                    delivery_method=(
                        delivery_method
                    )
                )
            )

            st.session_state.gap_report = (
                refreshed_gap_report
            )

            refreshed_score = compute_devops_score(
                refreshed_gap_report["assets"],
                delivery_method,
                platform
            )

            st.session_state.score = (
                refreshed_score
            )

            st.session_state.maturity = (
                get_maturity_level(
                    refreshed_score
                )
            )

            st.session_state.assessment_report = (
                generate_devops_report(
                    specification,
                    refreshed_gap_report
                )
            )

            st.session_state.roadmap = (
                generate_devops_roadmap(
                    refreshed_gap_report
                )
            )

            st.info(
                "The assessment has been refreshed. "
                "Rerun or refresh the page to display "
                "the updated results."
            )

        except Exception as error:

            st.error(
                "The DevOps asset generation failed."
            )

            st.exception(error)

    if st.session_state.generated_assets_zip:
        st.download_button(
            label="Download generated DevOps assets",
            data=st.session_state.generated_assets_zip,
            file_name=st.session_state.generated_assets_zip_name,
            mime="application/zip"
        )