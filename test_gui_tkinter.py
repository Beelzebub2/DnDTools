import customtkinter as ctk

# Initialize main window
root = ctk.CTk()
root.title("Simulated Vertical Tabs")
root.geometry("600x800")

# Main horizontal tabs
horizontal_tabs = ctk.CTkTabview(root)
horizontal_tabs.pack(padx=20, pady=20, fill="both", expand=True)

# Add characters
horizontal_tabs.add("Character 1")
horizontal_tabs.add("Character 2")
horizontal_tabs.add("Character 3")

# Function to create vertical-like tabs
def create_vertical_ui(parent_frame, character_name):
    # Container frame with 2 columns
    container = ctk.CTkFrame(parent_frame)
    container.pack(fill="both", expand=True, padx=10, pady=10)

    # Navigation buttons (left side, like vertical tabs)
    nav_frame = ctk.CTkFrame(container, width=100)
    nav_frame.pack(side="left", fill="y", padx=(0, 10))

    content_frame = ctk.CTkFrame(container)
    content_frame.pack(side="left", fill="both", expand=True)

    # Function to switch content
    def show_content(index):
        for widget in content_frame.winfo_children():
            widget.destroy()
        label = ctk.CTkLabel(content_frame, text=f"{character_name} - View {index}", font=("Arial", 20))
        label.pack(pady=30)

    # Create buttons for 3 vertical sections
    for i in range(1, 4):
        btn = ctk.CTkButton(nav_frame, text=f"Stash {i}", width=80, command=lambda i=i: show_content(i))
        btn.pack(pady=10)

    # Show default content
    show_content(1)

# Build vertical UI for each horizontal tab
create_vertical_ui(horizontal_tabs.tab("Character 1"), "Character 1")
create_vertical_ui(horizontal_tabs.tab("Character 2"), "Character 2")
create_vertical_ui(horizontal_tabs.tab("Character 3"), "Character 3")

# Run the app
root.mainloop()
