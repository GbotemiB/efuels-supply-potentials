# SPDX-FileCopyrightText:  Open Energy Transition gGmbH
#
# SPDX-License-Identifier: AGPL-3.0-or-later

RESULTS_DIR = "plots/results/"
PYPSA_EARTH_DIR = "submodules/pypsa-earth/"


configfile: "submodules/pypsa-earth/config.default.yaml"
configfile: "submodules/pypsa-earth/configs/bundle_config.yaml"
configfile: "configs/config.main.yaml"


wildcard_constraints:
    simpl="[a-zA-Z0-9]*|all",
    clusters="[0-9]+(m|flex)?|all|min",
    ll="(v|c)([0-9\.]+|opt|all)|all",
    opts="[-+a-zA-Z0-9\.]*",
    unc="[-+a-zA-Z0-9\.]*",
    planning_horizon="[0-9]{4}",
    countries="[A-Z]{2}",


localrules:
    all,


rule validate:
    params:
        countries=config["validation"]["countries"],
        clusters=config["validation"]["clusters"],
        planning_horizon=config["validation"]["planning_horizon"],
    output:
        demand=RESULTS_DIR + "demand_validation_{clusters}_{countries}_{planning_horizon}.png",
        capacity=RESULTS_DIR + "capacity_validation_{clusters}_{countries}_{planning_horizon}.png",
        generation=RESULTS_DIR + "generation_validation_{clusters}_{countries}_{planning_horizon}.png",
        generation_detailed=RESULTS_DIR + "generation_validation_detailed_{clusters}_{countries}_{planning_horizon}.png",
    resources:
        mem_mb=16000,
    script:
        "plots/data_validation.py"


rule validate_all:
    input:
        expand(RESULTS_DIR
            + "demand_validation_{clusters}_{countries}_{planning_horizon}.png",
            **config["validation"],
        ),
        expand(RESULTS_DIR
            + "capacity_validation_{clusters}_{countries}_{planning_horizon}.png",
            **config["validation"],
        ),
        expand(RESULTS_DIR
            + "generation_validation_{clusters}_{countries}_{planning_horizon}.png",
            **config["validation"],
        ),
        expand(RESULTS_DIR
            + "generation_validation_detailed_{clusters}_{countries}_{planning_horizon}.png",
            **config["validation"],
        ),


rule retrieve_cutouts:
    params:
        countries=config["countries"],
    output:
        cutouts=PYPSA_EARTH_DIR+"cutouts/cutout-2013-era5.nc"
    resources:
        mem_mb=16000,
    script:
        "scripts/retrieve_cutouts.py"