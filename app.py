"""
Flask web application for the novel generator.

This web interface wraps the functionality of ``novel_generator.py`` into a
simple HTML form. Users can enter a story premise and adjust parameters such
as total word count, number of chapters, minimum and maximum words per chapter
and the Ollama model to use.  When the form is submitted, the application
invokes the generator functions to produce a novel and saves it as a Word
document in the ``static`` folder.  The result page then presents a link to
download the file.

Note: The generation process can take a long time (especially for tens of
chapters at tens of thousands of words each).  The application currently
executes everything synchronously during the request.  For a production
deployment, consider moving the heavy lifting into a background task queue.
"""

import os
from flask import Flask, render_template, request, send_from_directory, url_for

import novel_generator


app = Flask(__name__, static_folder='static', template_folder='templates')

# Ensure the static directory exists for saving output files.
os.makedirs(app.static_folder, exist_ok=True)


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # Extract form data with defaults.
        topic = request.form.get('topic', '').strip()
        if not topic:
            return render_template('index.html', error="Please provide a story premise.")
        try:
            word_count = int(request.form.get('word_count', 90000))
        except ValueError:
            word_count = 90000
        try:
            chapters_input = request.form.get('chapters', '').strip()
            chapters = int(chapters_input) if chapters_input else 0
        except ValueError:
            chapters = 0
        try:
            min_words = int(request.form.get('min_words', 2000))
        except ValueError:
            min_words = 2000
        try:
            max_words = int(request.form.get('max_words', 4000))
        except ValueError:
            max_words = 4000
        model = request.form.get('model', 'gpt-oss:20b').strip()
        output_filename = request.form.get('output', 'novel.docx').strip() or 'novel.docx'
        # Derive chapter count if not provided.
        avg_len = (min_words + max_words) // 2
        num_chapters = chapters or max(10, word_count // avg_len)
        # Generate outline.
        outline = novel_generator.generate_outline(topic, num_chapters, word_count, model=model)
        chapters_text = []
        summaries = []
        # Write each chapter.
        for idx, chapter_prompt in enumerate(outline):
            chapter_text, summary = novel_generator.generate_chapter(
                index=idx,
                chapter_prompt=chapter_prompt,
                topic=topic,
                prev_summaries=summaries,
                min_words=min_words,
                max_words=max_words,
                model=model,
            )
            chapters_text.append(chapter_text)
            summaries.append(summary)
        # Save the document in static folder.
        output_path = os.path.join(app.static_folder, output_filename)
        novel_generator.assemble_document(chapters_text, output_path)
        # Provide link to download.
        return render_template('result.html', filename=output_filename)
    # GET request – render form.
    return render_template('index.html')


@app.route('/static/<path:filename>')
def static_files(filename: str):
    return send_from_directory(app.static_folder, filename, as_attachment=True)


if __name__ == '__main__':
    # Running the app locally.
    app.run(debug=True, host='0.0.0.0', port=5000)