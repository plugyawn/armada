"""Personal pages (contract C5), over zerver/lib/armada_pages.py. The
routes are intentionally undocumented, like the beta's other armada/
routes."""

from django.http import HttpRequest, HttpResponse
from pydantic import Json

from zerver.lib.armada_pages import (
    access_page_person,
    create_post,
    delete_post,
    list_pages,
    read_page,
    read_post,
    update_bio,
    update_post,
)
from zerver.lib.response import json_success
from zerver.lib.typed_endpoint import PathOnly, typed_endpoint, typed_endpoint_without_parameters
from zerver.models import UserProfile


@typed_endpoint_without_parameters
def get_armada_pages(request: HttpRequest, user_profile: UserProfile) -> HttpResponse:
    return json_success(request, data={"pages": list_pages(user_profile)})


@typed_endpoint
def get_armada_page(
    request: HttpRequest, user_profile: UserProfile, *, user_id: PathOnly[int]
) -> HttpResponse:
    person = access_page_person(user_profile, user_id)
    return json_success(request, data=read_page(user_profile, person))


@typed_endpoint
def patch_armada_my_page(
    request: HttpRequest, user_profile: UserProfile, *, bio: str
) -> HttpResponse:
    update_bio(user_profile, bio)
    return json_success(request)


@typed_endpoint
def get_armada_post(
    request: HttpRequest, user_profile: UserProfile, *, post_id: PathOnly[int]
) -> HttpResponse:
    return json_success(request, data={"post": read_post(user_profile, post_id)})


@typed_endpoint
def post_armada_post(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    title: str,
    content: str,
    draft: Json[bool] = False,
) -> HttpResponse:
    post = create_post(user_profile, title, content, draft=draft)
    return json_success(request, data={"id": post.id})


@typed_endpoint
def patch_armada_post(
    request: HttpRequest,
    user_profile: UserProfile,
    *,
    post_id: PathOnly[int],
    title: str | None = None,
    content: str | None = None,
    draft: Json[bool] | None = None,
) -> HttpResponse:
    update_post(user_profile, post_id, title=title, content=content, draft=draft)
    return json_success(request)


@typed_endpoint
def delete_armada_post(
    request: HttpRequest, user_profile: UserProfile, *, post_id: PathOnly[int]
) -> HttpResponse:
    delete_post(user_profile, post_id)
    return json_success(request)
