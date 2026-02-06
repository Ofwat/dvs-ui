# dvs-ui

## Examples

- `examples/plotlydash-basic` – lightweight Dash app displaying an Iris scatter plot. See the example README for dependencies and usage notes.

## Running the Plotly Dash example

1. Install the pinned dependencies:

   ```
   pip install -r requirements.txt
   ```

2. Start the app:

   ```
   python examples/plotlydash-basic/app.py
   ```

3. Open http://127.0.0.1:8050 in your browser to view the scatter plot.

The GOV.UK bundle lives in `examples/plotlydash-basic/assets`, so Dash now serves the CSS/JS/font/assets locally instead of pulling from a CDN. CSS files sit in `assets/stylesheets/`, JavaScript files live in `assets/javascripts/`, and the fonts/images/manifest live directly at `assets/`. Flask routes expose `/stylesheets/...` and `/javascripts/...` for the template, while the inline initialization script adds the GOV.UK body classes and calls `GOVUKFrontend.initAll()` so GOV.UK components behave like the docs site. The layout now matches the “precompiled files” HTML you shared by keeping only the GOV.UK template wrappers and hero content; every visual token comes from the GOV.UK assets.
