# Code for the paper "[A Nonlinear Data Assimilation Algorithm with Closed-Form Approximations for Multi-Layer Flow Fields](https://doi.org/10.1175/MWR-D-24-0277.1)" ##

<img src="./multistepDA.png" width="1000" />

## Structure
1. Generate QG simulations as the reference run ("truth"): [run_twolayer_qgtopo.m](./code/qg_2layer_topo).
2. Reproduce results and figures of paper: [MultistepDA.ipynb](./code). The computation demanding DA tasks are independent python scripts: [da_tracerobs.py](./code) for tracer observations and [da_flowobs.py](./code) for the upper-layer flow fully observed case.
3. The [data](./data) folder includes the example data for a short integration time of QG, which is used for the computational cost analysis.

## Paper
If you find the code useful, please consider citing the paper 
```
@article { ANonlinearDataAssimilationAlgorithmwithClosedFormApproximationsforMultilayerFlowFields,
      author = "Zhongrui Wang and Nan Chen and Di Qi",
      title = "A Nonlinear Data Assimilation Algorithm with Closed-Form Approximations for Multilayer Flow Fields",
      journal = "Monthly Weather Review",
      year = "2025",
      publisher = "American Meteorological Society",
      address = "Boston MA, USA",
      volume = "153",
      number = "12",
      doi = "10.1175/MWR-D-24-0277.1",
      pages=      "2889 - 2912",
      url = "https://journals.ametsoc.org/view/journals/mwre/153/12/MWR-D-24-0277.1.xml"
}
```
