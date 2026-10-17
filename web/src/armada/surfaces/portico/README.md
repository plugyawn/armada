# surfaces/portico

The pages before the app: signing in and up, confirming an email, a password reset, finding an account, an organization's address, creating an organization, and the errors the server draws. They are Jinja templates that extend `templates/zerver/armada_portico.html` and build their parts with `templates/zerver/armada_portico_macros.html`; this directory holds their stylesheet (`portico.css`) and what the forms do in the page (`index.ts`: the window's ring, showing a password, a sign-in's fragment, a time zone). The `armada-portico` entry (`web/src/bundles/armada_portico.ts`) loads them with the tokens and the kit's base, controls, hover layer and glyphs.

Not here: a check of what was typed (the server's forms do it and draw their errors), a network call, or a page inside the app.

To add one: extend `zerver/armada_portico.html`, import the macros `with context`, and build the page from the classes in `portico.css`; a new part goes into `portico.css` and, if a second page needs it, into the macros.
