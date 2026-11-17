"""Using the DSMS Python SDK (dsms-sdk) in scripts or notebooks that Claude writes, without Claude seeing the token.

The script loads the credentials file at run time inside its own process. Claude writes and runs the code but only
sees what the script prints. The SDK keeps the token as a pydantic SecretStr, so printing the config shows '**********'.

    pip install dsms-sdk
    python examples/sdk_example.py
"""
from pathlib import Path

from dsms import DSMS

ENV = Path.home() / ".dsms" / "dsms.env"   # DSMS_HOST_URL and DSMS_TOKEN, see dsms.env.example

dsms = DSMS(env=str(ENV))                  # loads the file into this process only

rows = dsms.sparql_interface.query("""
    SELECT ?class (COUNT(?s) AS ?n) WHERE { ?s a ?class }
    GROUP BY ?class ORDER BY DESC(?n) LIMIT 10
""")
for b in rows["results"]["bindings"]:
    print(b["class"]["value"], b["n"]["value"])
