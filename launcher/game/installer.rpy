
# This file imports the extensions API into the default store, and makes it
# also contains the strings used by the extensions API, so the Ren'Py translation
# framework can find them.

init python:
    import installer

init python hide:
    _("Downloading [installer.download_file]...")
    _("Could not download [installer.download_file] from [installer.download_url]:\n{b}[installer.download_error]")
    _("The downloaded file [installer.download_file] from [installer.download_url] is not correct.")
    _("Unpacking [installer.unpack_archive]...")
    _("Could not run [installer.install_args!r]:\n[installer.install_error]")
    _("The manifest signature is not valid.")
    _("No project has been selected.")
    _("No project has been selected.")
