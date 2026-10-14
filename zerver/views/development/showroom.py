import os

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from zerver.lib.templates import ARMADA_PALETTES

background_colors = [
    {"name": "Default background", "css_var": "--color-background"},
    {"name": "Popover background", "css_var": "--color-background-popover-menu"},
    {"name": "Modal background", "css_var": "--color-background-modal"},
    {"name": "Compose background", "css_var": "--color-compose-box-background"},
]


def get_svg_filenames() -> list[str]:
    icons_dir = os.path.join(os.path.dirname(__file__), "../../../web/icons")

    # Get all .svg file names from the directory
    svg_files = [f for f in os.listdir(icons_dir) if f.endswith(".svg")]

    # Remove the .svg extension from the file names
    icon_names = [os.path.splitext(f)[0] for f in svg_files]

    # Sort the list alphabetically
    return sorted(icon_names)


def showroom_component_buttons(request: HttpRequest) -> HttpResponse:
    context = {
        "background_colors": background_colors,
        "icons": get_svg_filenames(),
        "page_is_showroom": True,
        "showroom_component": "buttons",
        "doc_root_title": "Button styles browser",
        # We set isolated_page to avoid clutter from footer/header.
        "isolated_page": True,
    }
    return render(request, "zerver/development/showroom/buttons.html", context)


def showroom_component_banners(request: HttpRequest) -> HttpResponse:
    context = {
        "background_colors": background_colors,
        "icons": get_svg_filenames(),
        "page_is_showroom": True,
        "showroom_component": "banners",
        "doc_root_title": "Banner styles browser",
        # We set isolated_page to avoid clutter from footer/header.
        "isolated_page": True,
    }
    return render(request, "zerver/development/showroom/banners.html", context)


def showroom_component_inputs(request: HttpRequest) -> HttpResponse:
    context = {
        "background_colors": background_colors,
        "icons": get_svg_filenames(),
        "page_is_showroom": True,
        "showroom_component": "inputs",
        "doc_root_title": "Input styles browser",
        # We set isolated_page to avoid clutter from footer/header.
        "isolated_page": True,
    }
    return render(request, "zerver/development/showroom/inputs.html", context)


# The name people see for the agent.  The showroom is the design's
# contract, so it shows the name the design is drawn with; the
# server's own constant is renamed with its models and routes.
ARMADA_SHOWROOM_AGENT_NAME = "Onyx"


def showroom_armada(request: HttpRequest) -> HttpResponse:
    """The kit of the rebuilt frontend: every component in every state,
    the tokens of every palette, the sprite, and the motion and sound
    benches.  `arm check kit`, `motion` and `design-lint` read this
    page."""
    sprite_path = os.path.join(settings.DEPLOY_ROOT, "web/src/armada/kit/icons/sprite.svg")
    with open(sprite_path) as sprite_file:
        sprite = sprite_file.read()
    context = {
        "agent_name": ARMADA_SHOWROOM_AGENT_NAME,
        "palettes": " ".join(ARMADA_PALETTES),
        "sprite": sprite,
        "doc_root_title": "Armada showroom",
        "isolated_page": True,
    }
    return render(request, "zerver/development/showroom/armada.html", context)
