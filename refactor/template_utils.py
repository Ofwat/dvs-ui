from gov_uk_dashboards.template import read_template


def build_index_string() -> str:
    """Return the GOV.UK index string augmented with the rebranded class."""
    template = read_template()
    marker = 'class="govuk-template govuk-template--rebranded"'
    if marker in template:
        return template

    return template.replace(
        "<html ",
        '<html class="govuk-template govuk-template--rebranded" ',
        1,
    )
