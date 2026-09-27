# Hof Hans

This is a web application for farmer Hans which shows his fields and detection results. He can also pick a terminal and download a spray map in the exact format his terminal expects.

## Technologies Used
- Flask
- Leaflet
- Docker

## Features
- Overview of all fields -> list to scroll through on the left
- Interactive map linked to the field list -> on the right
- Field-specific detection results (where are the weeds, how much herbicide is saved) -> on the right of the map area, underneath the selection
- Selection of the farmer's terminal and sprayer section width -> top right of the map area
- Download of the spray map in the correct terminal format and folder structure -> next to terminal selection
- Smooth visualization of the big dataset 

## Data
Provided data is not included in this repository due to its size. Data has to go into data/ in the repository root.

## Getting Started
...

## Decisions & Assumptions
- Savings [%] depend on terminal and section width, default section width is 25 cm (finest, most savings)
- Big dataset is visualized using a bounding box for smoother pan/zoom