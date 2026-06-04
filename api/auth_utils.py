from typing import Dict, Optional

from fastapi import HTTPException
from requests import Session

from playwright.async_api import async_playwright, TimeoutError

AUTH_SESSIONS: Dict[str, Session] = {}
import logging


def require_client_id(client_id: Optional[str]) -> str:
    if not client_id:
        raise HTTPException(status_code=400, detail="Missing X-Client-Id header")
    return client_id


def store_client_session(client_id: str, session: Session) -> None:
    AUTH_SESSIONS[client_id] = session


def clear_client_auth(client_id: str) -> None:
    AUTH_SESSIONS.pop(client_id, None)


async def ensure_authenticated_session(client_id: str) -> Session:
    session = AUTH_SESSIONS.get(client_id)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return session


def bootstrap_selfserve_context(session: Session) -> None:
    """Warm key self-serve pages that commonly establish routing/session context."""
    session.get("https://esther.rice.edu/selfserve/", timeout=15)
    session.get("https://esther.rice.edu/selfserve/swkscmt.main", timeout=15)


async def authenticate_with_duo(netid: str, password: str):
    """
    Headless authentication: Server types credentials, user approves on phone.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://esther.rice.edu/")

        try:
            await page.fill("#username", netid)
            await page.fill("#password", password)
            # await page.click("button[name='eventId_proceed']")
            await page.keyboard.press("Enter")

            # Check if credentials are wrong before waiting on Duo.
            error_messages = [
                "The NetID you entered cannot be identified",
                "The password you entered was incorrect",
            ]
            try:
                await page.wait_for_function(
                    """(errors) => {
                        const text = document.body ? document.body.innerText : "";
                        return errors.some((msg) => text.includes(msg));
                    }""",
                    arg=error_messages,
                    timeout=4000,
                )
                raise Exception("Invalid credentials")
            except TimeoutError:
                pass

            # We wait up to 60 seconds for them to tap "Approve" on their phone
            try:
                await page.wait_for_selector(
                    "text='Personal Information'", timeout=60000
                )
            except TimeoutError:
                # Re-check for invalid credentials in case the error rendered late.
                content = await page.content()
                if any(msg in content for msg in error_messages):
                    raise Exception("Invalid credentials")
                raise Exception("Duo push timed out")

        except Exception as e:
            print("[AUTH] Failed! Taking screenshot of the browser state...")
            await page.screenshot(path="debug_duo_error.png", full_page=True)
            await browser.close()
            if str(e) == "Invalid credentials":
                raise HTTPException(status_code=401, detail="Invalid NetID or password")
            if str(e) == "Duo push timed out":
                raise HTTPException(
                    status_code=408,
                    detail="Duo push timed out. Please approve the request and try again.",
                )
            raise HTTPException(
                status_code=401,
                detail="Authentication failed. Did you approve the Duo push?",
            )

        # Extract cookies
        cookies = await context.cookies()
        cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies}

        await browser.close()

    # Create session after auth and load cookies into it
    session = Session()
    for name, value in cookie_dict.items():
        session.cookies.set(name, value)

    # Prime selfserve cookies
    try:
        bootstrap_selfserve_context(session)
    except Exception as e:
        print(f"[WARN] Failed to warm selfserve session after Duo auth: {str(e)}")

    return session
