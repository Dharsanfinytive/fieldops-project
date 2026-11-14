from fastapi import APIRouter, Depends, HTTPException, status, Response, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Union, Optional
from sqlalchemy import func, or_

from ..database import get_db
from .. import models, schemas
import uuid

from app.auth.dependencies import AuthenticatedUser, require_permission

from app.auth.rbac import Permission

router = APIRouter(
    prefix="/technicians",
    tags=["Technicians"]
)


def _normalized_contact_name(value: Optional[str]) -> str:
    """Normalize names only for legacy technician/profile linking."""
    return " ".join((value or "").split()).casefold()


def _attach_technician_contact_details(
    db: Session,
    technicians: list,
    tenant_id: str,
) -> None:
    """Attach profile-owned contact details to technician API records.

    TechnicianProfile is linked to User through user_id.
    Match by account ID first, then by unique full name for legacy records.
    """
    if not technicians:
        return

    technician_users = db.query(models.User).filter(
        models.User.tenant_id == tenant_id,
        func.lower(models.User.role) == "technician",
        models.User.deleted_at.is_(None),
    ).all()

    profile_rows = db.query(models.TechnicianProfile, models.User).join(
        models.User,
        models.User.id == models.TechnicianProfile.user_id,
    ).filter(
        models.TechnicianProfile.tenant_id == tenant_id,
        models.User.tenant_id == tenant_id,
        func.lower(models.User.role) == "technician",
        models.User.deleted_at.is_(None),
    ).all()

    users_by_id = {
        str(user.id): user for user in technician_users
    }

    profiles_by_user_id = {
        str(profile.user_id): profile
        for profile, _user in profile_rows
    }

    users_by_name = {}
    profiles_by_name = {}

    for user in technician_users:
        name = _normalized_contact_name(user.full_name)
        if name:
            users_by_name.setdefault(name, []).append(user)

    for profile, user in profile_rows:
        name = _normalized_contact_name(profile.full_name)
        if name:
            profiles_by_name.setdefault(name, []).append((profile, user))

    for technician in technicians:

        # First try the direct User ID relationship.
        user = (
            users_by_id.get(str(technician.tech_id))
            if technician.tech_id
            else None
        )

        profile = (
            profiles_by_user_id.get(str(user.id))
            if user
            else None
        )

        # Legacy fallback: match only when the name is unique.
        if user is None:
            name = _normalized_contact_name(
                technician.technician_name
            )

            matching_profiles = profiles_by_name.get(name, [])

            if len(matching_profiles) == 1:
                profile, user = matching_profiles[0]

            else:
                matching_users = users_by_name.get(name, [])

                if len(matching_users) == 1:
                    user = matching_users[0]
                    profile = profiles_by_user_id.get(str(user.id))

        profile_mobile = (
            profile.mobile_number.strip()
            if profile
            and profile.mobile_number
            and profile.mobile_number.strip()
            else None
        )

        account_mobile = (
            user.phone_number.strip()
            if user
            and user.phone_number
            and user.phone_number.strip()
            else None
        )

        legacy_mobile = (
            technician.phone_number.strip()
            if technician.phone_number
            and technician.phone_number.strip()
            else None
        )

        contact_mobile = (
            profile_mobile
            or account_mobile
            or legacy_mobile
        )

        # Return both field names for frontend compatibility.
        technician.phone_number = contact_mobile
        technician.mobile_number = contact_mobile
        technician.email = (
            user.email if user and user.email else None
        )

@router.post("", response_model=Union[schemas.TechnicianResponse, List[schemas.TechnicianResponse]], status_code=status.HTTP_200_OK)
def create_technician(
    technician: Union[schemas.TechnicianCreate, List[schemas.TechnicianCreate]],
    current_user: AuthenticatedUser = Depends(
    require_permission(Permission.TECHNICIANS_CREATE)
),
    db: Session = Depends(get_db)
):
    """
    Register one or more new technicians.
    Prevents duplicate entries based on name and skill.
    """
    tenant_id = current_user.tenant_id
    try:
        # Normalize to list for uniform processing
        tech_list = technician if isinstance(technician, list) else [technician]
        created_techs = []

        for tech_data in tech_list:
            # Check for duplicate
            existing = db.query(models.Technician).filter(
                models.Technician.technician_name == tech_data.technician_name,
                models.Technician.technician_skill == tech_data.technician_skill,
                models.Technician.tenant_id == tenant_id
            ).first()
            
            if existing:
                if not isinstance(technician, list):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Technician with name '{tech_data.technician_name}' and skill '{tech_data.technician_skill}' already exists"
                    )
                # For bulk, we skip duplicates to avoid failing the whole batch
                continue

            new_tech = models.Technician(
                tech_id=tech_data.tech_id or f"tech-{uuid.uuid4().hex[:8]}", # Use provided or auto-generate
                technician_name=tech_data.technician_name,
                technician_skill=tech_data.technician_skill,
                technician_location=tech_data.technician_location,
                technician_status=tech_data.technician_status,
                tenant_id=tenant_id
            )
            db.add(new_tech)
            created_techs.append(new_tech)

        db.commit()
        
        # Refresh and return
        for t in created_techs:
            db.refresh(t)
            
        if isinstance(technician, list):
            return created_techs
        else:
            if not created_techs: # Should not happen given logic above but for safety
                 raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Technician already exists")
            return created_techs[0]

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while creating technician: {str(e)}"
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred: {str(e)}"
        )




@router.get("", response_model=List[schemas.TechnicianResponse])
def get_all_technicians(
    response: Response,
    search: Optional[str] = None,
    status: Optional[str] = None,
    zone: Optional[str] = None,
    skill: Optional[str] = None,
    page: Optional[int] = Query(None, ge=1),
    limit: Optional[int] = Query(None, ge=1),
    current_user: AuthenticatedUser = Depends(
    require_permission(Permission.TECHNICIANS_VIEW_ALL)
),
    db: Session = Depends(get_db)
):
    """
    Retrieve all registered technicians, optionally filtered.
    """
    
    try:
        query = db.query(models.Technician)
        query = query.filter(
    models.Technician.tenant_id == current_user.tenant_id)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (models.Technician.technician_name.ilike(search_pattern)) |
                (models.Technician.technician_skill.ilike(search_pattern)) |
                (models.Technician.technician_location.ilike(search_pattern))
            )
            
        if status and status.upper() != "ALL":
            query = query.filter(func.lower(models.Technician.technician_status) == status.lower())
        else:
            query = query.filter(or_(
                models.Technician.technician_status.is_(None),
                func.upper(models.Technician.technician_status) != "DEACTIVATED",
            ))
            
        if zone and zone.upper() != "ALL":
            query = query.filter(func.lower(models.Technician.technician_location) == zone.lower())
            
        if skill and skill.upper() != "ALL":
            query = query.filter(func.lower(models.Technician.technician_skill) == skill.lower())
            
        total_count = query.count()
        response.headers["X-Total-Count"] = str(total_count)
        response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"
        
        query = query.order_by(models.Technician.technician_id.desc())
        if page and limit:
            query = query.offset((page - 1) * limit).limit(limit)
            
        technicians = query.all()
        # Technician profile data is the current contact source. Match by account
        # ID first, then by a unique profile/user name for older technician rows
        # that predate linked tech IDs.
        _attach_technician_contact_details(
            db,
            technicians,
            current_user.tenant_id
        )
        # The persisted counter may contain legacy assignment-based values.
        # Workload is the number of accepted jobs that remain unfinished.
        from ..workload_utils import count_accepted_open_jobs
        for technician in technicians:
            technician.current_jobs = count_accepted_open_jobs(
                db, technician.technician_id
            )
        return technicians
    except SQLAlchemyError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database error while fetching technicians: {str(e)}"
        )

@router.get("/workload", response_model=schemas.WorkloadResponse)
def get_technician_workload(technician_id: int,
    current_user: AuthenticatedUser = Depends(
    require_permission(Permission.TECHNICIANS_VIEW_ALL)
    ), db: Session = Depends(get_db)):
    """
    Retrieve workload details of a specific technician.
    """
    tech = db.query(models.Technician).filter(
    models.Technician.technician_id == technician_id,
    models.Technician.tenant_id == current_user.tenant_id
).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    
    from ..workload_utils import count_accepted_open_jobs
    return {
        "technician": tech.technician_name,
        "current_jobs": count_accepted_open_jobs(db, technician_id),
        "status": tech.technician_status
    }


@router.put("/update-workload", response_model=schemas.WorkloadResponse)
def update_technician_workload(
    update: schemas.WorkloadUpdate,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_MANAGE)
    ),
    db: Session = Depends(get_db)
):
    """
    Manually update technician workload and synchronize status.
    """
    from ..workload_utils import sync_technician_status
    
    tech = db.query(models.Technician).filter(
        models.Technician.technician_id == update.technician_id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    
    if update.current_jobs < 0:
        raise HTTPException(status_code=400, detail="Workload count cannot be negative")
        
    tech.current_jobs = update.current_jobs
    sync_technician_status(tech)
    
    db.commit()
    db.refresh(tech)
    
    return {
        "technician": tech.technician_name,
        "current_jobs": tech.current_jobs,
        "status": tech.technician_status
    }


@router.put("/update-status", response_model=schemas.TechnicianResponse)
def update_technician_status(
    update: schemas.TechnicianStatusUpdate,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_MANAGE)
    ),
    db: Session = Depends(get_db)
):
    """
    Manually update technician availability status.
    """
    tech = db.query(models.Technician).filter(
        models.Technician.technician_id == update.technician_id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Technician not found")
        
    tech.technician_status = update.status
    
    try:
        db.commit()
        db.refresh(tech)
        return tech
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error: {str(e)}")


@router.get("/validate-workload", response_model=schemas.WorkloadValidationResponse)
def validate_technician_workload_api(
    technician_id: int,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_VIEW_ALL)
    ),
    db: Session = Depends(get_db)
):
    """
    Validate technician workload conditions and return detailed status.
    """
    from ..validation import get_workload_validation_status
    
    tech = db.query(models.Technician).filter(
        models.Technician.technician_id == technician_id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
        
    from ..workload_utils import count_accepted_open_jobs
    tech.current_jobs = count_accepted_open_jobs(db, technician_id)
    return get_workload_validation_status(tech)


@router.get("/available", response_model=List[schemas.AvailableTechnicianResponse])
def get_available_technicians(
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_VIEW_ALL)
    ),
    db: Session = Depends(get_db)
):
    """
    Retrieve available technicians for assignment.
    Includes tenant isolation fallback and all candidate statuses so candidate selection is never empty.
    """
    query = db.query(models.Technician).filter(
        models.Technician.tenant_id == current_user.tenant_id
    )
    
    # Try fetching available or assigned technicians first
    available_query = query.filter(
        models.Technician.technician_status.in_(["AVAILABLE", "ASSIGNED", "Available", "Assigned"])
    )
    techs = available_query.all()
    
    # If no active/available technicians found, fallback to fetching all technicians
    if not techs:
        techs = query.all()
    
    result = []
    from ..workload_utils import count_accepted_open_jobs
    for tech in techs:
        current_jobs = count_accepted_open_jobs(db, tech.technician_id)
        is_eligible = (current_jobs < min(tech.max_jobs or 5, 5)) and ((tech.technician_status or "").upper() in ["AVAILABLE", "ASSIGNED"])
        result.append({
            "technician_id": tech.technician_id,
            "technician": tech.technician_name,
            "skill": tech.technician_skill,
            "location": tech.technician_location,
            "status": tech.technician_status,
            "current_jobs": current_jobs,
            "max_jobs": min(tech.max_jobs or 5, 5),
            "eligible_for_assignment": is_eligible
        })
        
    return result


@router.get("/zones", response_model=List[str])
def get_all_zones(
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_VIEW_ALL)
    ),
    db: Session = Depends(get_db)
):
    """
    Retrieve all unique technician zones/locations.
    """
    try:
        results = db.query(
            models.Technician.technician_location
        ).filter(
            models.Technician.tenant_id == current_user.tenant_id
        ).distinct().all()
        # Filter out empty or null locations, trim, and sort
        zones = sorted(list(set(r[0].strip() for r in results if r[0] and r[0].strip())))
        return zones
    except SQLAlchemyError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database error while fetching zones: {str(e)}"
        )


@router.get("/{technician_id}", response_model=schemas.TechnicianResponse)
def get_technician_by_id(
    technician_id: int,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_VIEW_ALL)
    ),
    db: Session = Depends(get_db)
):
    """
    Retrieve details of a specific technician.
    """
    tech = db.query(models.Technician).filter(
        models.Technician.technician_id == technician_id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")

    _attach_technician_contact_details(
        db,
        [tech],
        current_user.tenant_id
    )

    return tech


@router.put("/{id}/availability")
def update_technician_availability(
    id: str,
    update_data: schemas.TechnicianAvailabilityUpdate,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_MANAGE)
    ),
    db: Session = Depends(get_db)
):
    """
    Update the availability status of a technician by their integer ID or string tech_id.
    """
    tech = None
    if id.isdigit():
        tech = db.query(models.Technician).filter(
            models.Technician.technician_id == int(id),
            models.Technician.tenant_id == current_user.tenant_id
        ).first()
    if not tech:
        tech = db.query(models.Technician).filter(
            models.Technician.tech_id == id,
            models.Technician.tenant_id == current_user.tenant_id
        ).first()
        
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    
    # Validation is handled by Pydantic Literal
    tech.technician_status = update_data.technician_status
    db.commit()
    db.refresh(tech)
    
    return {
        "message": "Technician availability updated successfully",
        "technician": {
            "id": tech.technician_id,
            "tech_id": tech.tech_id,
            "name": tech.technician_name,
            "technician_status": tech.technician_status
        }
    }

from ..schemas import NotificationPreferencesInput, PreferencesUpdateResponse
from ..services.preferences import get_technician_preferences, update_technician_preferences, DEFAULT_PREFS
from datetime import datetime, timezone

@router.get("/{id}/preferences")
async def get_preferences(
    id: str,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_VIEW_ALL)
    ),
    db: Session = Depends(get_db)
):
    tech = db.query(models.Technician).filter(
        models.Technician.tech_id == id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")

    prefs = get_technician_preferences(db, id)
    return {
        "tech_id": id,
        "preferences": prefs,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "self"
    }

@router.patch("/{id}/preferences", response_model=PreferencesUpdateResponse)
async def update_preferences(
    id: str,
    payload: NotificationPreferencesInput,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_MANAGE)
    ),
    db: Session = Depends(get_db)
):
    try:
        tech = db.query(models.Technician).filter(
            models.Technician.tech_id == id,
            models.Technician.tenant_id == current_user.tenant_id
        ).first()
        if not tech:
            raise HTTPException(status_code=404, detail="Technician not found")

        updated_prefs = update_technician_preferences(
            db=db,
            tech_id=id,
            new_prefs=payload.model_dump(),
            updated_by="self"
        )
        if not updated_prefs:
            raise HTTPException(status_code=404, detail="Technician not found")
            
        return {
            "tech_id": id,
            "preferences": updated_prefs,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "updated_by": "self"
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "VALIDATION_FAILED", "message": str(e)})

@router.post("/{id}/preferences/reset", response_model=PreferencesUpdateResponse)
async def reset_preferences(
    id: str,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_MANAGE)
    ),
    db: Session = Depends(get_db)
):
    tech = db.query(models.Technician).filter(
        models.Technician.tech_id == id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")

    updated_prefs = update_technician_preferences(
        db=db,
        tech_id=id,
        new_prefs=DEFAULT_PREFS,
        updated_by="admin_reset"
    )
    if not updated_prefs:
        raise HTTPException(status_code=404, detail="Technician not found")
        
    return {
        "tech_id": id,
        "preferences": updated_prefs,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": "admin_reset"
    }

@router.put("/{technician_id}", response_model=schemas.TechnicianResponse)
def update_technician(
    technician_id: int,
    technician: schemas.TechnicianCreate,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_MANAGE)
    ),
    db: Session = Depends(get_db)
):
    """
    Update a technician's details.
    """
    tech = db.query(models.Technician).filter(
        models.Technician.technician_id == technician_id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    
    tech.technician_name = technician.technician_name
    tech.technician_skill = technician.technician_skill
    tech.technician_location = technician.technician_location
    tech.technician_status = technician.technician_status
    
    try:
        db.commit()
        db.refresh(tech)
        return tech
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.delete("/{technician_id}", status_code=status.HTTP_200_OK)
def delete_technician(
    technician_id: int,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_MANAGE)
    ),
    db: Session = Depends(get_db)
):
    """
    Delete a technician.
    """
    tech = db.query(models.Technician).filter(
        models.Technician.technician_id == technician_id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    
    try:
        db.delete(tech)
        db.commit()
        return {"message": "Technician deleted successfully"}
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@router.put("/{technician_id}/status")
def update_technician_status_by_id(
    technician_id: int,
    update_data: schemas.TechnicianAvailabilityUpdate,
    current_user: AuthenticatedUser = Depends(
        require_permission(Permission.TECHNICIANS_MANAGE)
    ),
    db: Session = Depends(get_db)
):
    """
    Update the status of a technician.
    """
    tech = db.query(models.Technician).filter(
        models.Technician.technician_id == technician_id,
        models.Technician.tenant_id == current_user.tenant_id
    ).first()
    if not tech:
        raise HTTPException(status_code=404, detail="Technician not found")
    
    tech.technician_status = update_data.technician_status
    
    try:
        db.commit()
        db.refresh(tech)
        return {
            "message": "Technician status updated successfully",
            "technician": {
                "id": tech.technician_id,
                "name": tech.technician_name,
                "technician_status": tech.technician_status
            }
        }
    except SQLAlchemyError as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


