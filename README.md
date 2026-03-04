Healthcare Dealer and distributor Coversational Lead chatbot
-->This project is a GenAI-powered Healthcare Dealer & Distributor Lead System.
It:
-Scrapes real medical products from OpenFDA APIs and Netmeds 
-Stores products inside SQLite database
-Uses Gemini AI for conversational chatbot
-Captures dealer/distributor leads
-Sends email notifications
-Supports bulk product and distributor queries

What This Project Does:
- Scrapes real medicines (OpenFDA Drug API)
 -Scrapes real medical devices (OpenFDA Device API)
 -Scrapes OTC products (Netmeds website)
 -Stores everything in SQLite database
 -Loads Knowledge Base for chatbot (RAG concept)
 -Uses Gemini AI to answer human language questions
-Captures dealer/distributor lead information
-Sends email alerts

Project Structure
project/
│
├── app.py              → Main Flask server
├── agent.py            → Gemini AI conversation logic
├── database.py         → SQLite database setup
├── scraper.py          → Real product scraping engine
├── email_service.py    → Email sending logic
├── requirements.txt    → Required packages
│
├── static/          → Frontend HTML files
│
└── healthcare.db       → Database (auto-created)

Technologies Used
-Flask	Backend web server
-SQLite	Database
-Google Gemini	AI chatbot
-Requests	API calls
-BeautifulSoup	Web scraping
-OpenFDA API	Real drug & device data
-Netmeds	OTC product scraping

Step 1: Install Python
     --Install Python 3.10+
	 
Step 2: Install Requirements
     --Inside project folder:
          ---pip install -r requirements.txt

     --If virtual environment:
          ---python -m venv venvvenv\Scripts\activate
          ---pip install -r requirements.txt

step 3:Create .env File
    --Create a file named .env
    --Add:
        GEMINI_API_KEY=your_gemini_api_key
        EMAIL_USER=your_email@gmail.com
        EMAIL_PASS=your_email_password

Step 4: Initialize Database
  --Run:
      ---python database.py
  This creates:
       -products table
       -knowledge_base table
       -leads table
	   
Step 5: Run the Application
  --Start server:
     ---python app.py
  You will see:
     --Running on http://127.0.0.1:5000
  Open browser:
     ---http://127.0.0.1:5000

Step 6:Run Scraper (Important)
   --Scraper collects real healthcare products.
Automatically runs on startup OR manually:
  --http://127.0.0.1:5000/api/scrape
  It collects:
    -Drug data from OpenFDA
    -Device data from OpenFDA
    -OTC products from Netmeds
    -Loads chatbot knowledge base

Step 7: Test Chatbot
  Ask:
     What categories available?
     How many products?
     Do you supply antibiotics?
     I want to become distributor
     Bulk order discount?
     Dealer partnership details?
 It understands natural language.

Step 8: Lead Capture
When user gives:
  -Name
  -Company
  -Phone
  -Email
  -Requirement
It:
  Saves in database
  Sends email notification
  Marks as qualified lead
