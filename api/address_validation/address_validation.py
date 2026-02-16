from __future__ import annotations

import os
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

load_dotenv()
router = APIRouter()

GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class AddressValidationRequest(BaseModel):
    address: str = Field(..., min_length=1)


class AddressValidationResponse(BaseModel):
    # "Valid" here means: Google returned *some* closest match.
    is_valid: bool

    original_address: str
    corrected_address: str

    # Helpful diagnostics
    did_change: bool
    accuracy_type: str | None = None
    accuracy_score: float | None = None


def _extract_best_result(geocode_result: dict[str, Any]) -> dict[str, Any] | None:
    results = geocode_result.get("results")
    if isinstance(results, list) and results:
        return results[0]

    return None


def _extract_formatted_address(result: dict[str, Any]) -> str | None:
    formatted = result.get("formatted_address")
    if isinstance(formatted, str) and formatted.strip():
        return formatted.strip()

    return None


def _normalize(s: str) -> str:
    # conservative normalization
    return " ".join(s.lower().split())


def _extract_accuracy(result: dict[str, Any]) -> tuple[str | None, float | None]:
    """
    Google Geocoding fields (commonly):
    - geometry.location_type: string (ROOFTOP, RANGE_INTERPOLATED, GEOMETRIC_CENTER, APPROXIMATE)

    Google does not provide a numeric confidence score in Geocoding API responses,
    so accuracy_score is always None.
    """
    geometry = result.get("geometry")
    location_type: str | None = None
    if isinstance(geometry, dict):
        raw_location_type = geometry.get("location_type")
        if isinstance(raw_location_type, str):
            location_type = raw_location_type.strip() or None

    return (location_type, None)


@router.post("/address/validate", response_model=AddressValidationResponse)
async def validate_address(payload: AddressValidationRequest) -> AddressValidationResponse:
    """
    Behavior:
    - If Google Geocoding returns any candidate result, we return it as the "closest match".
    - If Google returns no results, 404.
    - Always returns corrected_address (from Google's formatted_address).
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GOOGLE_MAPS_API_KEY is not configured")

    original = payload.address.strip()
    print(original)
    if not original:
        raise HTTPException(status_code=400, detail="Address cannot be empty")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                GOOGLE_GEOCODE_URL,
                params={
                    "address": original,
                    "key": api_key,
                },
            )
            response.raise_for_status()
            geocode_result = response.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Google Geocoding request failed: {exc}")

    status = geocode_result.get("status")
    if status != "OK":
        if status == "ZERO_RESULTS":
            raise HTTPException(status_code=404, detail="No matching address found")
        error_message = geocode_result.get("error_message")
        details = f"{status}: {error_message}" if error_message else str(status)
        raise HTTPException(status_code=502, detail=f"Google Geocoding error: {details}")

    best = _extract_best_result(geocode_result)
    if not best:
        raise HTTPException(status_code=404, detail="No matching address found")

    corrected = _extract_formatted_address(best)
    if not corrected:
        raise HTTPException(status_code=502, detail="Unable to parse Google Geocoding response")

    did_change = _normalize(original) != _normalize(corrected)
    accuracy_type, accuracy_score = _extract_accuracy(best)

    # "valid" means "a candidate match exists".
    is_valid = True

    return AddressValidationResponse(
        is_valid=is_valid,
        original_address=original,
        corrected_address=corrected,
        did_change=did_change,
        accuracy_type=accuracy_type,
        accuracy_score=accuracy_score,
    )
