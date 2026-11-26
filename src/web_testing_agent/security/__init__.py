"""Security study: Katalyze's explorer as the navigation front end of a black-box scanner.

Detection is OWASP ZAP's own (its passive and active scan rules, unchanged); the tester only
decides where the browser goes, and its traffic reaches ZAP through ZAP's proxy. The
baseline is ZAP's own crawlers (traditional and AJAX spider) feeding the same scanners, so a
difference in what is found comes from navigation alone. See `campaign.py`.
"""
