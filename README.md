# PokéSearcher

## Why Does This Website Exist?

PokéSearcher was created to help prevent the annoying need to have to go through different pages or websites to find Pokémon stats, moves or sprites. Websites like Serebii and Bulbapedia have a lot of useful information, but it can take quite a few clicks and time to find the specific thing you need. This website was designed to put this information in one place using a simple search bar, tabs and easy-to-use buttons. There is even a way to compare two Pokémon side by side, so yopu don't have to keep switching between tabs when comparing stats or building a team (the Pokémon Champions grind to Master Ball can be tough).

## Features

- **Pokémon:** Search for a Pokémon and view its Pokédex information, types, stats and forms.

<img width="2556" height="1438" alt="image" src="https://github.com/user-attachments/assets/01be392e-5ea5-4fd0-9d8e-033aea94f277" />

- **Moves:** Search for a move and view details such as its type, power, accuracy, PP and priority.

<img width="2559" height="1438" alt="image" src="https://github.com/user-attachments/assets/9a406005-b35f-4f50-bdc1-aee06f0a5e19" />

- **Abilities:** Search for an ability and view its effect, the games it's in and the Pokémon that have it.
- 
<img width="2559" height="1438" alt="image" src="https://github.com/user-attachments/assets/0e27d5c3-b7d8-4d2b-ab8b-26a11c061eb5" />

- **Items:** Search for an item and view its effect, the games it's in, its location and price.
  
<img width="2556" height="1438" alt="image" src="https://github.com/user-attachments/assets/3ebeea34-9b98-45fa-9574-b7d841244e9c" />

- **Sprites:** Find a Pokémon's sprite by selecting the Pokémon and game, including shiny sprites and alternate forms when available.
  
<img width="2559" height="1438" alt="image" src="https://github.com/user-attachments/assets/7340b886-64ad-4ee4-9c2c-a8099b5fbd1c" />

- **Cards:** Search for Pokémon Trading Card Game cards and view their images and available pricing information.

<img width="2559" height="1438" alt="image" src="https://github.com/user-attachments/assets/b539c366-6d93-4529-9475-5e6dd8c2aead" />

- **Compare:** Compare two Pokémon, moves, abilities or items side by side, making it much easier to compare while building a competitive team.

<img width="2559" height="1438" alt="image" src="https://github.com/user-attachments/assets/70de5ce5-2970-48ae-bb77-798b30620ba1" />

- **Favourites:** Save specific game-centric or form-centric entries so they are easier to find again.

<img width="2556" height="1438" alt="image" src="https://github.com/user-attachments/assets/56d133c6-9fd7-4a42-a7a2-3ac91d2aad59" />

- **Search Filters and Suggestions:** Use filters and see suggestions while searching to make it easier to find Pokémon, items, moves, and abilities.

<img width="2559" height="1438" alt="image" src="https://github.com/user-attachments/assets/d8d4b7b9-1906-4a42-8bdb-525ba44882d0" />

- **Search Saved:** Computer crashed? Internet went out? Jump right back in where you left off, with last search.

<img width="2557" height="1198" alt="image" src="https://github.com/user-attachments/assets/32a21014-7cae-45b9-8081-7b40b25aac97" />

- **Responsive:** View the website from any aspect ratio, including on mobile.

<img width="747" height="1192" alt="image" src="https://github.com/user-attachments/assets/63782c96-c746-420d-a32e-550cee7cbca8" />

### Main page

The navigation bar lets me switch between the different sections, and the search field is where I can start looking up Pokémon information. There is even a light (Poké Ball) and dark (Luxury Ball) mode. Just hit the Poké Ball in the top-right corner to toggle between them.

<img width="1279" height="599" alt="Screenshot 2026-10-24 231113" src="https://github.com/user-attachments/assets/d3a56468-76af-45ec-8a54-870143241b41" />

<img width="2556" height="1197" alt="image" src="https://github.com/user-attachments/assets/9ef0810b-74b6-4203-8573-78a02eddfd69" />

## Setup

You will need Python 3.12 or newer installed on your computer and an internet connection to run the application and load information from the APIs. The steps below are for Windows PCs only.

1. Extract the project ZIP and open PowerShell in PyCharm in the folder containing manage.py.
2. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the required packages:

   ```powershell
   pip install -r requirements.txt
   ```

4. Create your local environment file by copying .env.example to .env:

   ```powershell
   Copy-Item .env.example .env
   ```

   The example values work for local development, so no editing should be required.

5. Prepare the database:

   ```powershell
   python manage.py migrate
   ```

6. Start the application:

   ```powershell
   python manage.py runserver
   ```

7. Open http://127.0.0.1:8000/ in your browser.

## Notes
Some pages can take a while to load due to the sheer amount of info needed to be pulled and loaded from the server database. Be a bit patient and give the site a second to load, it will respond at the very most after 10 seconds (for the Pokémon that The Pokémon Company loves to put in every game).

The website uses PokeAPI for Pokémon information and TCGdex for Pokémon Trading Card Game information. Some additional Pokémon information, such as Pokédex entries and move details, is retrieved from Bulbapedia. These sources do not require API keys or any other separate API setup for this project. Only an internet connection is required.
