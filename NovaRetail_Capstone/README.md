# NovaRetail Executive Deck Automation
 
## Overview
 
This project automates generation of a 5-slide executive Quarterly Business Review presentation for NovaRetail using Python and python-pptx.
 
The solution:
 
- Loads business data from Excel
- Calculates KPIs
- Generates executive insights
- Creates PowerPoint charts
- Inserts icons
- Populates template placeholders using shape names
- Produces a presentation-ready executive deck
 
## Files
 
### Input
 
- assets/novaretail_dataset.xlsx
- assets/novaretail_template.pptx
- assets/Icons/*.png
 
### Output
 
- output/NovaRetail_QBR_Q2_2026_v2.pptx
 
## Requirements
 
- Python 3.13+
- pandas
- openpyxl
- python-pptx
- Pillow
 
## Installation
 
```powershell
py -m pip install -r requirements.txt
```
 
## Run
 
```powershell
py generate_deck.py
```
 
## Features
 
- KPI calculation
- Executive summary generation
- Revenue trend chart
- Customer experience chart
- Operational risk chart
- Automated icon insertion
- Placeholder cleanup
- PowerPoint automation using python-pptx
 
## Author
 
Sneha Upadhyay