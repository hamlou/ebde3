import json
import os

def generate_static_library():
    # In a real environment, this would call the Gemini API.
    # For MVP, we generate a static structured JSON of 30 days of educational content.
    
    topics = [
        "The 1% Risk Rule",
        "Why Stop Losses Are Mandatory",
        "Understanding Market Liquidity",
        "Support vs Resistance",
        "The Danger of Revenge Trading",
        "Why 90% of Signal Groups Fail",
        "Position Sizing Math",
        "RSI Divergence Explained",
        "How to Read Order Blocks",
        "Trading Psychology: Fear of Missing Out (FOMO)"
    ] * 3 # Repeat to make 30 days

    library = []
    for i, topic in enumerate(topics):
        day = i + 1
        post = {
            "day": day,
            "topic": topic,
            "content": f"**Day {day}: {topic}**\n\nMost traders lose because they ignore {topic.lower()}. We rely on pure data, not emotion. Always calculate your risk before entering a trade. One bad emotional trade can wipe out weeks of gains.\n\n*⚠️ DISCLAIMER: This is educational content only. Not financial advice.*"
        }
        library.append(post)

    os.makedirs("../content", exist_ok=True)
    with open("../content/30_day_library.json", "w") as f:
        json.dump(library, f, indent=4)
        
    print(f"Generated 30-day content library at content/30_day_library.json")

if __name__ == "__main__":
    generate_static_library()
